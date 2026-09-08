"""Client BLE Mini Pocket Printer.

Protocole reconstitué depuis une capture btsnoop HCI :

    10 FF 20 F0                  -> modèle
    10 FF 20 F1                  -> firmware
    10 FF 50 F1                  -> batterie (2 octets, le second = pourcentage)
    10 FF 40                     -> état
    10 FF F1 03 + 12 octets nuls -> debut de travail
    1F 10 | bpr(2) | largeur(2) | taille(4) | <zlib>
    1B 4A nn                     -> avance papier
    10 FF F1 45                  -> fin de travail
    <- AA 0D 0A                  fin d'impression
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
import zlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from collections.abc import Callable

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import usb
from .addresses import ble_address, classic_address

_LOGGER = logging.getLogger(__name__)


class PrintCancelled(Exception):
    """Le travail a été retire de la file avant d'être imprime."""


class QueueFull(Exception):
    """La file d'attente a atteint sa limite."""


class PrintExpired(Exception):
    """Le travail a attendu trop longtemps pour rester pertinent."""


# Un contrôleur Bluetooth ne mène qu'une connexion à la fois de façon fiable :
# deux imprimantes qui impriment ensemble via le même adaptateur se gênent.
# Ce verrou partagé les sérialise, sans bloquer l'USB qui n'a pas ce défaut.
_VERROUS_RADIO: dict[str, asyncio.Lock] = {}


def _verrou_radio(cle: str) -> asyncio.Lock:
    if cle not in _VERROUS_RADIO:
        _VERROUS_RADIO[cle] = asyncio.Lock()
    return _VERROUS_RADIO[cle]

WRITE_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"

WIDTH_PX = 384
BYTES_PER_ROW = WIDTH_PX // 8
# L'en-tête 1F 10 ne porte pas de hauteur : elle se déduit de la taille
# decompressee. Un travail peut donc contenir autant de lignes que voulu, et
# l'application officielle n'envoyait 384 lignes que parce que c'etait sa
# taille de page. Decouper en plusieurs travaux desynchronise le firmware, qui
# imprime le second bloc comme des octets bruts.
MAX_ROWS = 0   # 0 = un seul travail, quelle que soit la hauteur
DEFAULT_CHUNK = 20

CMD_MODEL = bytes([0x10, 0xFF, 0x20, 0xF0])
CMD_FIRMWARE = bytes([0x10, 0xFF, 0x20, 0xF1])
CMD_BATTERY = bytes([0x10, 0xFF, 0x50, 0xF1])
CMD_STATUS = bytes([0x10, 0xFF, 0x40])
CMD_SERIAL = bytes([0x10, 0xFF, 0x20, 0xF2])

# Réglages, identifies en capturant l'application officielle :
#   10 FF 11        -> lit la densité      (1er octet, 0 a 2)
#   10 FF 10 00 nn  -> écrit la densité    -> "OK"
#   10 FF 13        -> lit la mise en veille en minutes
#   10 FF 12 00 nn  -> écrit la mise en veille -> "OK"
CMD_READ_DENSITY = bytes([0x10, 0xFF, 0x11])
CMD_READ_SLEEP = bytes([0x10, 0xFF, 0x13])
CMD_SET_DENSITY = bytes([0x10, 0xFF, 0x10, 0x00])
CMD_SET_SLEEP = bytes([0x10, 0xFF, 0x12, 0x00])

DENSITY_LEVELS = {0: "faible", 1: "moyenne", 2: "forte"}

# Octet renvoye par 10 FF 40 : 0x00 rouleau en place, 0x04 papier absent.
# Vérifié sur l'appareil, rouleau retire puis remis. Les autres bits restent
# inconnus : ils alimentent l'entite Anomalie, non le capteur papier.
STATUS_PAPER_OUT = 0x04
CMD_JOB_START = bytes([0x10, 0xFF, 0xF1, 0x03])
CMD_JOB_END = bytes([0x10, 0xFF, 0xF1, 0x45])
PADDING = bytes(12)
BITMAP_MAGIC = bytes([0x1F, 0x10])
DONE_MARKER = bytes([0xAA, 0x0D, 0x0A])


def encode_bitmap(rows: list[bytes]) -> bytes:
    """Entete 1F 10 suivie de la charge utile zlib (wbits=10, comme la capture)."""
    raw = b"".join(rows)
    compressor = zlib.compressobj(9, zlib.DEFLATED, 10)
    payload = compressor.compress(raw) + compressor.flush()
    return BITMAP_MAGIC + struct.pack(">HHI", BYTES_PER_ROW, WIDTH_PX, len(payload)) + payload


class MiniPocketPrinter:
    """Une connexion à la fois : l'imprimante n'accepté qu'un seul lien."""

    def __init__(self, hass: HomeAssistant, address: str,
                 queue_limit: int = 5, transport: str = "ble",
                 usb_path: str | None = None, job_ttl: int = 0) -> None:
        self._hass = hass
        self.address = address
        # Renseigné uniquement si l'utilisateur accepté le suivi par nom.
        self._client: BleakClientWithServiceCache | None = None
        # asyncio.Lock sert de file d'attente : deux automatisations
        # simultanees s'enchainent au lieu d'échouer, dans l'ordre d'arrivee.
        self._lock = asyncio.Lock()
        self._queued = 0
        # Une lecture occupe la liaison autant qu'une impression : la
        # signaler évite que l'interface paraisse inerte pendant un relevé.
        self._lecture_en_cours = False
        # Incremente à chaque annulation : les travaux qui attendent le
        # verrou comparent ce jeton a celui note avant leur mise en file et
        # renoncent si quelqu'un a vide la file entre-temps.
        self._cancel_token = 0
        # Abonnes prevenus des qu'une lecture aboutit : sans cela, les
        # entites gardent l'affichage précédent jusqu'à leur propre minuteur.
        self._listeners: list[Callable[[], None]] = []
        # 0 = illimite. Au-dela, un nouveau travail est refusé plutot que de
        # laisser une imprimante hors de portée accumuler des tickets qui
        # sortiraient tous d'un coup à son retour.
        self.queue_limit = queue_limit
        # Heures au-delà desquelles un travail en attente n'a plus de sens :
        # un ticket météo sorti six heures plus tard n'intéresse personne.
        # 0 désactive la péremption.
        self.job_ttl = job_ttl
        # "ble", "usb" ou "auto" : USB quand un périphérique est
        # configuré, BLE sinon.
        self.transport = transport
        # Chemin du périphérique USB, ex. /dev/usb/lp0 ou usb:001:007.
        self.usb_path = usb_path or None
        self._usb: usb.UsbConnection | None = None
        # Garde-fou contre les tentatives simultanees : deux connexions en
        # vol vers le même périphérique donnent EALREADY côté noyau.
        self._connecting = False
        self._rx = bytearray()
        self._event = asyncio.Event()
        self._chunk = DEFAULT_CHUNK
        self._no_response = False
        self._notify = False
        # Mémorisé un refus d'abonnement : inutile de rouvrir une connexion
        # à chaque cycle pour lire une batterie qu'on ne peut pas lire.
        self.notify_refused = False
        self.battery: int | None = None
        self.last_print: datetime | None = None
        # Octet renvoye par 10 FF 40. Dans la capture d'origine il valait
        # toujours 00, imprimante prete et papier en place : la signification
        # des bits reste à confirmer sur ton matériel.
        self.status_raw: int | None = None
        self.serial: str | None = None
        # Valeur de l'octet de statut lors de la dernière impression réussie.
        self.status_baseline: int | None = None
        self.density: int | None = None
        self.sleep_minutes: int | None = None
        self.last_error: str | None = None
        self.model: str | None = None
        self.firmware: str | None = None

    # -- connexion ----------------------------------------------------------

    @property
    def paper_ok(self) -> bool | None:
        """Rouleau en place. None tant que le statut n'a pas été lu."""
        if self.status_raw is None:
            return None
        return not self.status_raw & STATUS_PAPER_OUT

    @property
    def other_fault(self) -> bool | None:
        """Bits allumes hors de celui du papier, signification inconnue."""
        if self.status_raw is None:
            return None
        return bool(self.status_raw & ~STATUS_PAPER_OUT & 0xFF)

    @property
    def status_bits_changed(self) -> int | None:
        """Ecart avec la référence apprise, utile au diagnostic."""
        if self.status_raw is None or self.status_baseline is None:
            return None
        return self.status_raw ^ self.status_baseline

    @property
    def rssi(self) -> int | None:
        """Meilleure puissance reçue, tous points d'ecoute confondus."""
        best = self.scanners
        return best[0]["rssi"] if best else None

    @property
    def scanners(self) -> list[dict]:
        """Ou l'imprimante est entendue, du plus fort au plus faible.

        Home Assistant route la connexion vers le point d'ecoute au meilleur
        signal : savoir lesquels la percoivent, et a quelle puissance, est ce
        qui permet de placer un proxy utilement.
        """
        seen: list[dict] = []
        for address in {self.address, classic_address(self.address)}:
            for info in bluetooth.async_scanner_devices_by_address(
                self._hass, address, connectable=False
            ):
                scanner = info.scanner
                seen.append({
                    "source": getattr(scanner, "name", None)
                    or getattr(scanner, "source", "?"),
                    "adapter": getattr(scanner, "source", None),
                    "rssi": info.advertisement.rssi,
                    "connectable": getattr(scanner, "connectable", None),
                    "address": info.ble_device.address,
                })
        seen.sort(key=lambda item: item["rssi"] or -127, reverse=True)
        return seen

    @property
    def busy(self) -> bool:
        """Vrai pendant une impression ou une lecture : le lien est occupé."""
        return self._lock.locked()

    @property
    def reading(self) -> bool:
        """Vrai pendant un relevé, par opposition à une impression."""
        return self._lecture_en_cours

    @property
    def queued(self) -> int:
        """Travaux en attente derriere celui en cours."""
        return max(0, self._queued - (1 if self._lock.locked() else 0))

    @property
    def pending(self) -> int:
        """Total pris en charge : celui en cours plus ceux qui attendent."""
        return self._queued + (1 if self._lecture_en_cours else 0)

    @property
    def status(self) -> str:
        """État resume, sans ouvrir de connexion."""
        if self.busy:
            return f"impression ({self.queued} en attente)" if self.queued else "impression"
        if self.paper_ok is False:
            return "papier absent"
        if self.other_fault:
            return f"anomalie (statut 0x{self.status_raw:02x})"
        if not self.available:
            return "injoignable"
        if self.notify_refused:
            return "pret (sans notifications)"
        return "pret"

    @property
    def available(self) -> bool:
        """Disponibilité apparente de l'imprimante.

        En BLE elle se déduit des annonces reçues ; en USB un périphérique
        branché est toujours joignable, et l'échec survient à la connexion.
        """
        if self.transport == "usb" or (self.transport == "auto"
                                       and self.usb_path):
            # Un périphérique branche est toujours joignable.
            return True
        return self._resolve_device() is not None

    def _resolve_device(self):
        """Retrouve l'imprimante parmi les annonces reçues.

        L'entrée peut avoir été créée depuis l'une ou l'autre face : on
        cherche donc l'adresse BLE correspondante, pas celle enregistrée.
        """
        for candidate in (ble_address(self.address), self.address):
            device = bluetooth.async_ble_device_from_address(
                self._hass, candidate, connectable=True
            )
            if device is not None:
                return device
        return None

    async def _connect_with_backoff(self, rounds: int = 3) -> None:
        """Réessaie après une pause : l'imprimante refusé toute connexion
        pendant quelques secondes après une impression, le temps que le lien
        précédent soit vraiment libéré.
        """
        for attempt in range(rounds):
            try:
                await self._connect()
                return
            except (BleakError, HomeAssistantError, OSError) as err:
                if attempt == rounds - 1:
                    raise
                _LOGGER.debug(
                    "Connexion a %s refusee (%s), nouvelle tentative dans %ds",
                    self.address, err, 5 * (attempt + 1),
                )
                await asyncio.sleep(5 * (attempt + 1))

    @property
    def active_transport(self) -> str:
        """Transport de la connexion en cours, ou celui qui sera tente."""
        if self._usb is not None:
            return "usb"
        if self._client is not None:
            return "ble"
        if self.transport == "auto":
            if self.usb_path:
                return "usb"
            return "ble"
        return self.transport

    @property
    def _passe_par_la_radio(self) -> bool:
        """Vrai si l'opération va mobiliser un contrôleur Bluetooth."""
        if self.transport == "usb":
            return False
        if self.transport == "auto" and self.usb_path:
            return False
        return True

    @asynccontextmanager
    async def _session(self, lecture: bool = False):
        """Réserve l'imprimante, et la radio quand elle est en jeu."""
        if lecture:
            self._lecture_en_cours = True
            self.notify_listeners()
        try:
            async with self._lock:
                if self._passe_par_la_radio:
                    async with _verrou_radio("bluetooth"):
                        yield
                else:
                    yield
        finally:
            if lecture:
                self._lecture_en_cours = False
                self.notify_listeners()

    async def _connect(self, max_attempts: int = 3) -> None:
        """Ouvre le lien, avec repli d'un transport sur l'autre en mode auto.

        En auto, l'ordre suit la fiabilité constatée : USB d'abord quand un
        périphérique est configuré, puis BLE. Le Bluetooth classique ne vient
        qu'en dernier recours, et seulement si l'imprimante y est appairée :
        l'essayer d'emblée coûtait plusieurs secondes à chaque ticket pour un
        transport qui aboutit rarement.
        """
        if self.transport == "usb":
            await self._connect_via("usb", max_attempts)
            return

        if self.transport == "auto":
            order = ["ble"]
            if self.usb_path:
                # L'USB ne dépend ni de la portée ni d'un appairage : quand
                # il est configuré, il passe avant tout le reste.
                order.insert(0, "usb")
            errors = []
            for position, choice in enumerate(order):
                # Une seule tentative sur le transport mémorisé : après un
                # deplacement il est forcément le mauvais, et insister
                # retarde la bascule de plusieurs dizaines de secondes.
                attempts = 1 if position == 0 else max_attempts
                try:
                    await self._connect_via(choice, attempts)
                    if self._usb is None and self._client is None:
                        raise HomeAssistantError(
                            f"{choice} : connexion annoncee mais aucun lien"
                        )
                    if choice != order[0]:
                        _LOGGER.info("%s : bascule sur le transport %s",
                                     self.address, choice.upper())
                    return
                except (BleakError, HomeAssistantError, OSError) as err:
                    errors.append(f"{choice} : {err}")
                    _LOGGER.debug("%s : %s a échoué (%s)", self.address,
                                  choice, err)
            raise HomeAssistantError(
                f"Aucun transport n'aboutit vers {self.address}. "
                + " | ".join(errors)
            )

        await self._connect_via(
            "ble", max_attempts
        )

    async def _connect_via(self, choice: str, max_attempts: int = 3) -> None:
        if self._connecting:
            raise HomeAssistantError(
                f"Une connexion vers {self.address} est deja en cours"
            )
        self._connecting = True
        try:
            await self._connect_via_inner(choice, max_attempts)
        finally:
            self._connecting = False

    async def _connect_via_inner(self, choice: str, max_attempts: int = 3) -> None:
        # Un transport précédent peut avoir laisse un objet en place : le
        # fermer évite d'écrire sur un lien mort et de melanger les chemins.
        await self._disconnect()

        if choice == "usb":
            if not self.usb_path:
                raise HomeAssistantError(
                    "Aucun peripherique USB selectionne dans les options"
                )
            self._usb = usb.UsbConnection(self._hass, self.usb_path)
            try:
                await self._usb.connect()
            except OSError as err:
                self._usb = None
                raise HomeAssistantError(
                    f"Peripherique USB {self.usb_path} inaccessible : {err}"
                ) from err
            # Rien a activer : les réponses arrivent sur le même flux.
            self._notify = True
            self.notify_refused = False
            self._chunk = usb.CHUNK
            self._no_response = False
            return

        # Résolution à chaque connexion : l'imprimante s'endort, disparait,
        # et peut reapparaitre sous une autre adresse.
        device = self._resolve_device()
        if device is None:
            raise HomeAssistantError(
                f"Imprimante {self.address} injoignable : elle est probablement "
                "eteinte, endormie ou hors de portee. Allumez-la et reessayez."
            )
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self.address,
            self._on_disconnect,
            use_services_cache=True,
            max_attempts=max_attempts,
        )
        await self._async_start_notify()
        # L'imprimante ignoré ce qu'on lui envoie dans les premieres
        # centaines de millisecondes suivant l'abonnement.
        await asyncio.sleep(0.3)

        mtu = getattr(self._client, "mtu_size", 0) or 23
        self._chunk = max(DEFAULT_CHUNK, min(mtu - 3, 244))
        char = self._client.services.get_characteristic(WRITE_UUID)
        self._no_response = bool(char and "write-without-response" in char.properties)

    async def _async_start_notify(self) -> None:
        """Activé les notifications, en tentant un appairage si besoin.

        Certaines de ces imprimantes exigent un lien chiffre pour écrire le
        descripteur de notification : l'écriture répond alors ATT 0x08,
        Insufficient Authorization. L'impression, elle, n'a pas besoin des
        notifications : on continue sans plutot que d'échouer.
        """
        try:
            await self._client.start_notify(NOTIFY_UUID, self._on_notify)
            self._notify = True
            self.notify_refused = False
            return
        except BleakError as err:
            _LOGGER.debug("start_notify refuse (%s), tentative d'appairage", err)

        try:
            if await self._client.pair():
                await self._client.start_notify(NOTIFY_UUID, self._on_notify)
                self._notify = True
                self.notify_refused = False
                return
        except (BleakError, NotImplementedError, AttributeError) as err:
            _LOGGER.debug("Appairage impossible : %s", err)

        self._notify = False
        # Marque le dernier essai, sans condamner les suivants : sur une
        # liaison instable, un refus passager ne doit pas bloquer les
        # lectures jusqu'àu prochain redemarrage.
        self.notify_refused = True
        _LOGGER.warning(
            "Notifications indisponibles sur %s : impression en aveugle, "
            "sans etat ni niveau de batterie",
            self.address,
        )

    def _on_disconnect(self, _client) -> None:
        """Le lien est tombe : l'écriture suivante doit échouer proprement."""
        if self._client is not None:
            _LOGGER.debug("%s : lien BLE ferme par le peripherique",
                          self.address)
        self._client = None

    def _on_notify(self, _sender, data: bytearray) -> None:
        self._rx += data
        self._event.set()

    async def _disconnect(self) -> None:
        if self._usb is not None:
            connection, self._usb = self._usb, None
            try:
                await connection.close()
            except Exception:  # noqa: BLE001 - fermeture best effort
                _LOGGER.debug("Fermeture USB imparfaite", exc_info=True)
            return

        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 - déconnexion best effort
                _LOGGER.debug("Deconnexion imparfaite", exc_info=True)

    # -- primitives ---------------------------------------------------------

    async def _send(self, data: bytes, bulk: bool = False, delay: float = 0.01) -> None:
        if self._usb is None and self._client is None:
            # Sans lien établi, écrire ne produirait qu'une erreur d'attribut
            # illisible. Le cas survient quand une connexion a échoue sans
            # que l'appelant l'ait vu.
            raise HomeAssistantError(
                f"Aucune connexion ouverte vers {self.address}"
            )

        if self._usb is not None:
            await self._usb.send(data, delay=delay)
            return

        without = self._no_response and bulk
        for index in range(0, len(data), self._chunk):
            await self._client.write_gatt_char(
                WRITE_UUID, data[index:index + self._chunk], response=not without
            )
            if delay:
                await asyncio.sleep(delay)

    async def _query(self, cmd: bytes, timeout: float = 3.0) -> bytes:
        if self._usb is None and self._client is None:
            raise HomeAssistantError(
                f"Aucune connexion ouverte vers {self.address}"
            )

        if self._usb is not None:
            return await self._usb.query(cmd, timeout=timeout)

        if not self._notify:
            await self._send(cmd, delay=0)
            return b""
        self._rx.clear()
        self._event.clear()
        await self._send(cmd, delay=0)
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            return b""
        await asyncio.sleep(0.15)
        return bytes(self._rx)

    async def _wait_done(self, timeout: float = 30.0) -> bool:
        if self._usb is not None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            buffer = bytearray()
            while loop.time() < deadline:
                chunk = await self._usb.read(timeout=1.0)
                if chunk:
                    buffer += chunk
                    if DONE_MARKER in bytes(buffer):
                        return True
            # Sur /dev/usb/lp0 le pilote usblp ne remonte pas toujours les
            # données entrantes : l'absence de AA 0D 0A ne prouve pas que
            # l'impression a échoue. Le travail est parti en entier, on le
            # considère abouti plutot que de perdre l'horodatage.
            _LOGGER.debug(
                "%s : fin d'impression non confirmee en USB, travail "
                "considere comme abouti", self.address,
            )
            return True

        if not self._notify:
            return True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if DONE_MARKER in bytes(self._rx):
                return True
            self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), 1.0)
            except asyncio.TimeoutError:
                continue
        return DONE_MARKER in bytes(self._rx)

    # -- opérations publiques ----------------------------------------------

    async def async_update(self) -> None:
        """Relit tout ce qui est affiché, en une seule connexion.

        Un relevé ne doit pas s'eterniser : si l'imprimante ne s'annonce pas,
        on ne tente rien, et une seule tentative de connexion suffit pour un
        rafraichissement de fond.
        """
        if not self.available:
            raise HomeAssistantError(f"Imprimante {self.address} hors de portee")

        async with self._session(lecture=True):
            try:
                # Un proxy ESPHome raté souvent la première tentative
                # (ESP_GATT_ERROR) et aboutit à la seconde.
                try:
                    await self._connect(max_attempts=3)
                except Exception:
                    self.clear_readings()
                    raise
                if not self._notify:
                    raise HomeAssistantError(
                        f"Notifications refusees par {self.address} : niveau de "
                        "batterie illisible"
                    )
                self.model = (await self._query(CMD_MODEL)).decode("ascii", "replace").strip()
                self.firmware = (await self._query(CMD_FIRMWARE)).decode("ascii", "replace").strip()

                status = await self._query(CMD_STATUS)
                self.status_raw = status[0] if status else None
                if not self.serial:
                    serial = await self._query(CMD_SERIAL)
                    if serial:
                        self.serial = serial.decode("ascii", "replace").strip()

                density = await self._query(CMD_READ_DENSITY)
                self.density = density[0] if density else None
                sleep = await self._query(CMD_READ_SLEEP)
                self.sleep_minutes = sleep[0] if sleep else None
                if density == b"" or sleep == b"":
                    _LOGGER.warning(
                        "%s : lecture des reglages sans reponse "
                        "(densite=%s, veille=%s)",
                        self.address, density.hex() or "vide",
                        sleep.hex() or "vide",
                    )

                battery = await self._query(CMD_BATTERY)
                if not battery:
                    # Première requête parfois perdue au réveil : on retente.
                    battery = await self._query(CMD_BATTERY)
                _LOGGER.info(
                    "%s : modele=%r firmware=%r batterie=%s",
                    self.address, self.model, self.firmware, battery.hex() or "(vide)",
                )
                if len(battery) >= 2:
                    self.battery = battery[-1]
                else:
                    raise HomeAssistantError(
                        f"Pas de reponse a la requete batterie sur {self.address}"
                    )
            finally:
                await self._disconnect()
                self.notify_listeners()

    def surveiller_presence(self) -> Callable[[], None]:
        """Prévient les entités dès que l'imprimante apparaît ou disparaît.

        Sans cela, une imprimante éteinte laisse ses entités affichées avec
        leurs dernières valeurs jusqu'au prochain événement.
        """
        annuler = [
            bluetooth.async_register_callback(
                self._hass,
                lambda info, change: self.notify_listeners(),
                {"address": self.address, "connectable": False},
                bluetooth.BluetoothScanningMode.ACTIVE,
            ),
            bluetooth.async_track_unavailable(
                self._hass,
                lambda info: self.notify_listeners(),
                self.address,
                connectable=False,
            ),
        ]

        def _retirer() -> None:
            for fonction in annuler:
                fonction()

        return _retirer

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Enregistré un abonne et retourne la fonction de desabonnement."""
        self._listeners.append(callback)

        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _remove

    def clear_readings(self) -> None:
        """Efface les valeurs lues : rien ne doit survivre à un échec.

        Sans cela, l'interface continue d'afficher un relevé ancien pendant
        que l'imprimante a change d'état, ce qui est pire qu'un champ vide.
        """
        self.battery = None
        self.density = None
        self.sleep_minutes = None
        self.status_raw = None

    def notify_listeners(self) -> None:
        for callback in list(self._listeners):
            callback()

    def cancel_queued(self) -> int:
        """Annulé les travaux en attente. Retourne le nombre abandonné.

        Le travail déjà en cours n'est pas interrompu : le firmware a reçu
        une partie du bitmap, couper au milieu laisserait l'imprimante dans
        un état batard.
        """
        pending = self.queued
        self._cancel_token += 1
        self.notify_listeners()
        if pending:
            _LOGGER.info("%s : %d travail(s) en attente annule(s)",
                         self.address, pending)
        return pending

    async def async_read_settings(self) -> None:
        """Relit densité et délai de mise en veille."""
        async with self._session(lecture=True):
            try:
                await self._connect(max_attempts=2)
                if not self._notify:
                    raise HomeAssistantError(
                        f"{self.address} refuse les notifications : reglages "
                        "illisibles"
                    )
                density = await self._query(CMD_READ_DENSITY)
                self.density = density[0] if density else None
                sleep = await self._query(CMD_READ_SLEEP)
                self.sleep_minutes = sleep[0] if sleep else None
                if self.density is None or self.sleep_minutes is None:
                    _LOGGER.warning(
                        "%s : reglages illisibles (densite=%s veille=%s)",
                        self.address, self.density, self.sleep_minutes,
                    )
                else:
                    _LOGGER.info("%s : densite=%s veille=%s min",
                                 self.address, self.density, self.sleep_minutes)
            finally:
                await self._disconnect()
                self.notify_listeners()

    async def _async_write_setting(self, command: bytes, value: int,
                                   read_back: bytes) -> int | None:
        """Écrit un réglage puis le relit dans la même connexion.

        La relecture n'est pas une precaution de style : sans elle, une
        écriture refusée par le firmware passerait inapercue et l'interface
        afficherait une valeur que l'imprimante n'a jamais adoptee.
        """
        async with self._session():
            try:
                await self._connect(max_attempts=2)
                payload = command + bytes([value & 0xFF])
                reply = await self._query(payload)
                _LOGGER.debug("%s : %s -> %s", self.address, payload.hex(),
                              reply.hex() or "(rien)")

                if not self._notify:
                    raise HomeAssistantError(
                        f"{self.address} refuse les notifications : le reglage "
                        "ne peut etre ni confirme ni relu"
                    )
                if reply[:2] != b"OK":
                    raise HomeAssistantError(
                        f"{self.address} a refuse le reglage "
                        f"(reponse {reply.hex() or 'vide'})"
                    )

                await asyncio.sleep(0.3)
                current = await self._query(read_back)
                _LOGGER.debug("%s : relecture %s -> %s", self.address,
                              read_back.hex(), current.hex() or "(rien)")
                if not current:
                    raise HomeAssistantError(
                        f"{self.address} : reglage ecrit mais relecture muette"
                    )
                if current[0] != value:
                    raise HomeAssistantError(
                        f"{self.address} : reglage non applique, l'imprimante "
                        f"annonce {current[0]} au lieu de {value}"
                    )
                return current[0]
            finally:
                await self._disconnect()
                self.notify_listeners()

    async def async_set_density(self, level: int) -> bool:
        """0 faible, 1 moyenne, 2 forte."""
        if level not in DENSITY_LEVELS:
            raise HomeAssistantError(f"Densite hors plage : {level}")
        self.density = await self._async_write_setting(
            CMD_SET_DENSITY, level, CMD_READ_DENSITY
        )
        return True

    async def async_set_sleep(self, minutes: int) -> bool:
        """Délai avant mise en veille, en minutes."""
        if not 0 <= minutes <= 255:
            raise HomeAssistantError(f"Delai hors plage : {minutes}")
        self.sleep_minutes = await self._async_write_setting(
            CMD_SET_SLEEP, minutes, CMD_READ_SLEEP
        )
        return True

    async def async_print(self, rows: list[bytes], feed: int = 0x50,
                          block: int = MAX_ROWS) -> bool:
        """Imprime des lignes de 48 octets en un seul travail par défaut."""
        if self.queue_limit and self._queued >= self.queue_limit:
            raise QueueFull(
                f"File pleine sur {self.address} : {self._queued} travail(s) "
                f"deja en attente, limite fixee a {self.queue_limit}"
            )

        token = self._cancel_token
        depose = time.time()
        self._queued += 1
        # La file bouge à l'entrée, à la prise du verrou et à la sortie :
        # sans notification a chacun de ces moments, le capteur n'affiché le
        # changement qu'au prochain cycle d'une minute.
        self.notify_listeners()
        try:
            return await self._async_print_guarded(rows, feed, block, token,
                                                   depose)
        finally:
            self._queued -= 1
            self.notify_listeners()

    async def _async_print_guarded(self, rows: list[bytes], feed: int,
                                   block: int, token: int = 0,
                                   depose: float | None = None) -> bool:
        try:
            done = await self._async_print(rows, feed, block, token=token,
                                           depose=depose)
        except PrintExpired as err:
            _LOGGER.warning("%s : %s", self.address, err)
            return False
        except PrintCancelled:
            # Annulation volontaire : ni erreur a mémoriser, ni reprise.
            return False
        except BleakError as err:
            # Une rafale d'écritures sans accuse peut saturer le proxy ou le
            # firmware : le lien tombe en pleine transmission (GATT 133). On
            # rejoue le travail en écritures confirmees, plus lentes mais
            # regulees par la pile elle-même.
            _LOGGER.warning(
                "Impression interrompue sur %s (%s), reprise en mode sur",
                self.address, err,
            )
            await asyncio.sleep(3)
            try:
                done = await self._async_print(rows, feed, block, safe=True,
                                               token=token, depose=depose)
            except Exception as retry_err:  # noqa: BLE001
                self.last_error = str(retry_err)
                raise
        except Exception as err:  # noqa: BLE001 - trace conservée pour le capteur
            self.last_error = str(err)
            raise
        if done:
            self.last_print = datetime.now(timezone.utc)
            self.last_error = None
            # Une impression qui aboutit implique du papier : on retient la
            # valeur de statut correspondante comme référence.
            if self.status_raw is not None:
                self.status_baseline = self.status_raw
        self.notify_listeners()
        return done

    async def _async_print(self, rows: list[bytes], feed: int,
                           block: int, safe: bool = False,
                           token: int | None = None,
                           depose: float | None = None) -> bool:
        async with self._session():
            self.notify_listeners()
            if token is not None and token != self._cancel_token:
                _LOGGER.debug("%s : travail annule pendant l'attente", self.address)
                raise PrintCancelled("Travail annule avant son impression")

            try:
                await self._connect_with_backoff()
                if safe:
                    # Écritures confirmees : la pile attend chaque acquittement
                    # au lieu d'empiler les paquets.
                    self._no_response = False
                if block and block > 0:
                    blocks = [rows[i:i + block] for i in range(0, len(rows), block)]
                else:
                    blocks = [rows]
                blocks = blocks or [[]]
                for index, chunk in enumerate(blocks):
                    last = index == len(blocks) - 1
                    status = await self._query(CMD_STATUS, timeout=2.0)
                    if status:
                        self.status_raw = status[0]
                        if status[0] & STATUS_PAPER_OUT:
                            _LOGGER.warning(
                                "%s : plus de papier (statut 0x%02x)",
                                self.address, status[0],
                            )
                        elif status[0]:
                            _LOGGER.warning(
                                "%s : statut inhabituel 0x%02x",
                                self.address, status[0],
                            )
                    await self._send(CMD_JOB_START, delay=0.05)
                    await self._send(PADDING, delay=0.05)

                    self._rx.clear()
                    self._event.clear()
                    await self._send(encode_bitmap(chunk), bulk=True,
                                     delay=0.02 if safe else 0.005)

                    # L'imprimante demarre le moteur des le bitmap reçu et
                    # devient peu reactive : les dernières commandes peuvent
                    # échouer (GATT 133) alors que le travail est bien parti.
                    await asyncio.sleep(0.3)
                    try:
                        if feed and last:
                            await self._send(bytes([0x1B, 0x4A, feed & 0xFF]),
                                             bulk=True, delay=0.05)
                        await self._send(CMD_JOB_END, bulk=True, delay=0)
                    except BleakError as err:
                        _LOGGER.debug(
                            "Fin de travail non confirmee sur %s : %s",
                            self.address, err,
                        )
                        return True

                    if not self._notify:
                        # Sans notification de fin, on attend la duree
                        # d'impression : environ 8 points par millimetre a
                        # une centaine de lignes par seconde.
                        await asyncio.sleep(2.0 + len(chunk) / 100)
                        continue

                    if not await self._wait_done():
                        return False
                    if not last:
                        # Le firmware a besoin de souffler entre deux travaux.
                        await asyncio.sleep(1.0)

                if self._notify:
                    battery = await self._query(CMD_BATTERY)
                    if len(battery) >= 2:
                        self.battery = battery[-1]
                    # La connexion est ouverte : on rafraichit les réglages,
                    # sinon l'interface garde indefiniment une valeur périmée.
                    density = await self._query(CMD_READ_DENSITY)
                    self.density = density[0] if density else None
                    sleep = await self._query(CMD_READ_SLEEP)
                    self.sleep_minutes = sleep[0] if sleep else None
                return True
            finally:
                await self._disconnect()

    async def async_touch(self) -> bool:
        """Interrogation périodique : rafraichit les valeurs et repousse la veille.

        Une seule connexion sert les deux besoins. Elle lit tout ce qui est
        affiché, pour qu'aucune valeur ne provienne d'un relevé ancien, et
        remet à zéro le compteur d'inactivite du firmware.
        """
        if self.busy or self._connecting:
            # Une impression ou une autre lecture est en cours : inutile
            # d'ouvrir un second lien, l'imprimante n'en accepte qu'un.
            _LOGGER.debug("%s : interrogation reportee, liaison occupee",
                          self.address)
            return False
        if not self.available:
            _LOGGER.debug("%s : interrogation reportee, imprimante absente",
                          self.address)
            return False

        async with self._session(lecture=True):
            try:
                await self._connect(max_attempts=1)
                status = await self._query(CMD_STATUS, timeout=2.0)
                if status:
                    self.status_raw = status[0]
                if self._notify:
                    battery = await self._query(CMD_BATTERY)
                    self.battery = battery[-1] if len(battery) >= 2 else None
                    density = await self._query(CMD_READ_DENSITY)
                    self.density = density[0] if density else None
                    sleep = await self._query(CMD_READ_SLEEP)
                    self.sleep_minutes = sleep[0] if sleep else None
                _LOGGER.info(
                    "%s : interrogation periodique, batterie=%s statut=%s",
                    self.address, self.battery,
                    None if self.status_raw is None else hex(self.status_raw),
                )
                return True
            finally:
                await self._disconnect()
                self.notify_listeners()

    async def async_feed(self, dots: int) -> bool:
        """1B 4A doit être encadre par un travail, sinon le moteur ne tourne pas."""
        return await self.async_print([bytes(BYTES_PER_ROW)], feed=min(dots, 255))
