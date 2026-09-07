"""Transport Bluetooth classique (SPP / RFCOMM).

Home Assistant ne gère que le BLE : ce module ouvre un socket RFCOMM
directement, comme l'application officielle.

Prérequis : module rfcomm dans le noyau, CPython compilé avec le support
Bluetooth, et imprimante appairée dans BlueZ. is_supported() les vérifie.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import re
import socket

_LOGGER = logging.getLogger(__name__)

RFCOMM_CHANNEL = 1          # canal annonce en SDP par l'imprimante
CHUNK = 255                 # taille des écritures, comme l'application
# Un connect trop long laisse une tentative en cours côté noyau : la suivante
# échoue alors en EALREADY. Mieux vaut abandonner tot et réessayer.
CONNECT_TIMEOUT = 12.0
BUSY_ERRORS = (errno.EBUSY, errno.EALREADY, errno.EINPROGRESS)


def is_supported() -> bool:
    """Vrai si cette machine peut ouvrir un socket RFCOMM."""
    if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(socket, "BTPROTO_RFCOMM"):
        return False
    try:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                             socket.BTPROTO_RFCOMM)
    except OSError:
        return False
    sock.close()
    return True


BDADDR_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def normalize_address(address: str | None) -> str | None:
    """Met une adresse en forme, ou retourne None si elle est inexploitable.

    Le champ est saisi à la main : espaces, tirets ou minuscules sont
    fréquents, et le noyau refusé tout ce qui n'est pas strictement
    XX:XX:XX:XX:XX:XX en majuscules.
    """
    if not address:
        return None
    cleaned = address.strip().replace("-", ":").replace(" ", "").upper()
    if len(cleaned) == 12 and ":" not in cleaned:
        cleaned = ":".join(cleaned[i:i + 2] for i in range(0, 12, 2))
    return cleaned if BDADDR_RE.match(cleaned) else None


def classic_address(address: str) -> str:
    """Déduit l'adresse classique de l'adresse BLE.

    Ces imprimantes exposent deux adresses qui ne different que par leur
    premier octet : 5E:55:09:... en BLE, 55:55:09:... en classique.
    """
    parts = address.upper().split(":")
    if len(parts) == 6 and parts[0] == "5E":
        return ":".join(["55"] + parts[1:])
    return address.upper()


def ble_address(address: str) -> str:
    """Déduit l'adresse BLE de l'adresse classique.

    Symetrique de classic_address : une entrée créée depuis la face classique
    doit rester utilisable en BLE, sinon le repli ne peut pas fonctionner.
    """
    parts = address.upper().split(":")
    if len(parts) == 6 and parts[0] == "55":
        return ":".join(["5E"] + parts[1:])
    return address.upper()


def list_adapters(hass=None) -> list[dict]:
    """Contrôleurs Bluetooth locaux, avec leur nom et leur adresse.

    Trois sources, de la plus fiable à la plus générique :

      - les entrées de l'intégration bluetooth de Home Assistant, dont
        l'identifiant unique est l'adresse du contrôleur : c'est la seule
        source disponible depuis le conteneur ;
      - la bibliothèque bluetooth_adapters, déjà installée par cette même
        intégration ;
      - /sys/class/bluetooth, qui n'est pas monte dans le conteneur mais
        rend le module utilisable hors de Home Assistant.

    Les proxys ESPHome sont absents : ils ne relaient pas le classique.
    """
    adapters: dict[str, str] = {}

    if hass is not None:
        for entry in hass.config_entries.async_entries("bluetooth"):
            address = (entry.unique_id or "").upper()
            if normalize_address(address):
                adapters[address] = entry.title or address

    if not adapters:
        try:
            from bluetooth_adapters import get_adapters

            details = get_adapters()
            for name, info in (details or {}).items():
                address = str(info.get("address", "")).upper()
                if normalize_address(address):
                    adapters[address] = name
        except Exception:  # noqa: BLE001 - bibliothèque absente ou API différente
            _LOGGER.debug("bluetooth_adapters indisponible", exc_info=True)

    if not adapters:
        import glob
        import os

        for path in sorted(glob.glob("/sys/class/bluetooth/hci*")):
            try:
                with open(os.path.join(path, "address")) as handle:
                    address = handle.read().strip().upper()
            except OSError:
                continue
            if normalize_address(address):
                adapters[address] = os.path.basename(path)

    return [{"address": address, "name": name}
            for address, name in sorted(adapters.items(), key=lambda x: x[1])]


async def async_pairing_state(hass, address: str) -> dict:
    """Ou l'imprimante est appairée, d'après BlueZ.

    Interroge le D-Bus, la même source que l'intégration Bluetooth de Home
    Assistant. Retourne un dict : 'paired' liste les adresses de contrôleurs
    ou l'appairage existe, 'connected' ceux qui tiennent déjà un lien.

    Savoir cela avant d'ouvrir le socket évite les deux échecs les plus
    fréquents : tenter depuis un contrôleur sans clé, ou depuis un
    contrôleur dont le lien est déjà pris.
    """
    target = classic_address(address)
    state = {"paired": [], "connected": [], "known": False}

    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
    except ImportError:
        _LOGGER.debug("dbus_fast absent : etat d'appairage inconnu")
        return state

    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await bus.introspect("org.bluez", "/")
        proxy = bus.get_proxy_object("org.bluez", "/", introspection)
        manager = proxy.get_interface("org.freedesktop.DBus.ObjectManager")
        objects = await manager.call_get_managed_objects()
    except Exception:  # noqa: BLE001 - D-Bus indisponible dans ce conteneur
        _LOGGER.debug("Interrogation BlueZ impossible", exc_info=True)
        return state

    for path, interfaces in objects.items():
        device = interfaces.get("org.bluez.Device1")
        if not device:
            continue
        found = str(device.get("Address").value).upper()
        if found != target:
            continue

        state["known"] = True
        # /org/bluez/hci1/dev_55_55_09_13_85_57 -> hci1
        adapter_path = "/".join(path.split("/")[:4])
        adapter = interfaces.get("org.bluez.Adapter1")
        adapter_address = None
        if adapter is None:
            for other_path, other in objects.items():
                if other_path == adapter_path and "org.bluez.Adapter1" in other:
                    adapter_address = str(
                        other["org.bluez.Adapter1"]["Address"].value
                    ).upper()
        if adapter_address is None:
            controller = objects.get(adapter_path, {}).get("org.bluez.Adapter1")
            if controller:
                adapter_address = str(controller["Address"].value).upper()
        adapter_address = adapter_address or adapter_path.split("/")[-1]

        if device.get("Paired") and device["Paired"].value:
            state["paired"].append(adapter_address)
        if device.get("Connected") and device["Connected"].value:
            state["connected"].append(adapter_address)

    try:
        bus.disconnect()
    except Exception:  # noqa: BLE001
        pass
    return state


class SppConnection:
    """Connexion RFCOMM. Les appels bloquants partent dans l'executeur."""

    def __init__(self, hass, address: str, channel: int = RFCOMM_CHANNEL,
                 local_adapter: str | None = None) -> None:
        self._hass = hass
        self.address = classic_address(address)
        if normalize_address(self.address) is None:
            raise ValueError(f"Adresse imprimante invalide : {address!r}")
        self._channel = channel
        self._local = normalize_address(local_adapter)
        if local_adapter and self._local is None:
            _LOGGER.warning(
                "Adaptateur %r ignore : adresse invalide, attendu "
                "XX:XX:XX:XX:XX:XX", local_adapter,
            )
        self._sock: socket.socket | None = None

    async def connect(self, retries: int = 1) -> None:
        """Ouvre le canal, en reessayant si le noyau le dit occupe.

        EBUSY signifie qu'une liaison RFCOMM existe déjà vers ce périphérique.
        Elle se libéré en une poignee de secondes une fois le socket précédent
        ferme, d'ou la nouvelle tentative.
        """
        last: OSError | None = None
        for attempt in range(retries + 1):
            try:
                self._sock = await self._hass.async_add_executor_job(self._open)
                _LOGGER.debug("SPP connecte a %s canal %d", self.address,
                              self._channel)
                return
            except OSError as err:
                last = err
                if err.errno not in BUSY_ERRORS or attempt == retries:
                    raise
                _LOGGER.debug(
                    "Canal RFCOMM occupe (%s), nouvelle tentative dans 4 s",
                    err.errno,
                )
                await asyncio.sleep(4)
        if last is not None:
            raise last

    def _open(self) -> socket.socket:
        """Créé, lie et connecte le socket. Ferme tout en cas d'échec.

        Un socket abandonné sans fermeture garde la liaison RFCOMM ouverte
        côté noyau : la tentative suivante échoue alors en EBUSY, et le
        problème s'aggrave à chaque essai.
        """
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                             socket.BTPROTO_RFCOMM)
        try:
            sock.settimeout(CONNECT_TIMEOUT)
            if self._local:
                # Sans liaison explicite, le noyau choisit le contrôleur :
                # avec plusieurs adaptateurs, il peut prendre celui qui n'a
                # pas la clé d'appairage.
                try:
                    sock.bind((self._local, 0))
                except OSError as err:
                    # Un échec de liaison ne doit pas condamner la connexion :
                    # on repart sur le choix automatique.
                    _LOGGER.warning(
                        "Liaison a l'adaptateur %s impossible (%s), "
                        "selection automatique", self._local, err,
                    )
            sock.connect((self.address, self._channel))
            sock.settimeout(2.0)
            return sock
        except BaseException:
            sock.close()
            raise

    async def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            return

        def _close() -> None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

        await self._hass.async_add_executor_job(_close)

    async def send(self, data: bytes, delay: float = 0.005) -> None:
        """Écrit par blocs, avec la même temporisation que l'application."""
        if self._sock is None:
            raise RuntimeError("Socket SPP ferme")
        for index in range(0, len(data), CHUNK):
            piece = data[index:index + CHUNK]
            await self._hass.async_add_executor_job(self._sock.sendall, piece)
            if delay:
                await asyncio.sleep(delay)

    async def read(self, timeout: float = 2.0, size: int = 256) -> bytes:
        """Lit ce qui arrive, ou rien si le délai expire."""
        if self._sock is None:
            raise RuntimeError("Socket SPP ferme")

        def _recv() -> bytes:
            self._sock.settimeout(timeout)
            try:
                return self._sock.recv(size)
            except (TimeoutError, socket.timeout):
                return b""

        return await self._hass.async_add_executor_job(_recv)

    async def query(self, payload: bytes, timeout: float = 2.0) -> bytes:
        """Envoie puis attend la réponse, en laissant venir les fragments."""
        await self.send(payload, delay=0)
        reply = await self.read(timeout=timeout)
        if reply:
            await asyncio.sleep(0.12)
            reply += await self.read(timeout=0.2)
        return reply
