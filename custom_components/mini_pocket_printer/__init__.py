"""Intégration Mini Pocket Printer.

Imprimante thermique Tronic 5890 (Lidl), pilotée en USB, en Bluetooth
classique ou en BLE. Protocole reconstitué par rétro-ingénierie.

Auteur : Maxime Maucourant <mmaucourant@gmail.com> (@MaxDomo)
"""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from aiohttp import ClientTimeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_JOB_TTL,
    CONF_KEEP_AWAKE,
    CONF_QUEUE_LIMIT,
    CONF_TRANSPORT,
    CONF_USB_PATH,
    DEFAULT_JOB_TTL,
    DEFAULT_KEEP_AWAKE,
    DEFAULT_QUEUE_LIMIT,
    DEFAULT_TRANSPORT,
    DOMAIN,
    SERVICE_CANCEL,
    SERVICE_FEED,
    SERVICE_PRINT_IMAGE,
    SERVICE_PRINT_BARCODE,
    SERVICE_PRINT_QR,
    SERVICE_PRINT_TABLE,
    SERVICE_PRINT_TEXT,
    SERVICE_PRINT_WEATHER,
)
from .printer import MiniPocketPrinter, QueueFull
from .code_render import build_barcode_rows, build_qr_rows
from .table_render import build_table_rows
from .text_render import columns_for, image_bytes_to_rows, load_font, text_to_rows
from .weather_render import build_weather_rows
from .weather_ticket import async_build_weather_ticket, async_collect_weather

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]

# Les services rendent la main sans attendre la fin de l'impression : un
# ticket peut prendre plusieurs dizaines de secondes, et une automatisation
# n'a pas à rester suspendue pendant ce temps. "wait" rétablit l'attente
# quand on veut savoir si le travail a abouti.
ATTENTE_SCHEMA = {vol.Optional("wait", default=False): cv.boolean}

TARGET_SCHEMA = {
    vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("entry_id"): cv.string,
}

PRINT_TEXT_SCHEMA = vol.Schema({
    **TARGET_SCHEMA,
    **ATTENTE_SCHEMA,
    vol.Required("text"): cv.string,
    vol.Optional("size", default=36): vol.All(int, vol.Range(min=8, max=96)),
    vol.Optional("font"): cv.string,
    vol.Optional("align", default="left"): vol.In(["left", "center", "right"]),
    vol.Optional("bold", default=False): cv.boolean,
    vol.Optional("spacing", default=6): vol.All(int, vol.Range(min=0, max=40)),
    vol.Optional("feed", default=80): vol.All(int, vol.Range(min=0, max=255)),
})

PRINT_TABLE_SCHEMA = vol.Schema({
    **TARGET_SCHEMA,
    **ATTENTE_SCHEMA,
    vol.Required("rows"): vol.All(cv.ensure_list, [vol.All(cv.ensure_list, [
        vol.Any(cv.string, int, float, None)
    ])]),
    vol.Optional("headers"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("title"): cv.string,
    vol.Optional("align"): vol.All(
        cv.ensure_list, [vol.In(["left", "center", "right"])]
    ),
    vol.Optional("size", default=30): vol.All(int, vol.Range(min=8, max=96)),
    vol.Optional("font"): cv.string,
    vol.Optional("bold", default=False): cv.boolean,
    vol.Optional("grid", default=True): cv.boolean,
    vol.Optional("feed", default=80): vol.All(int, vol.Range(min=0, max=255)),
})

PRINT_QR_SCHEMA = vol.Schema({
    **TARGET_SCHEMA,
    **ATTENTE_SCHEMA,
    vol.Required("data"): cv.string,
    vol.Optional("scale", default=8): vol.All(int, vol.Range(min=2, max=16)),
    vol.Optional("border", default=2): vol.All(int, vol.Range(min=0, max=8)),
    vol.Optional("error_correction", default="M"): vol.In(["L", "M", "Q", "H"]),
    vol.Optional("label"): cv.string,
    vol.Optional("label_size", default=26): vol.All(int, vol.Range(min=8, max=64)),
    vol.Optional("font"): cv.string,
    vol.Optional("feed", default=80): vol.All(int, vol.Range(min=0, max=255)),
})

PRINT_BARCODE_SCHEMA = vol.Schema({
    **TARGET_SCHEMA,
    **ATTENTE_SCHEMA,
    vol.Required("data"): cv.string,
    vol.Optional("symbology", default="code128"): cv.string,
    vol.Optional("height", default=90): vol.All(int, vol.Range(min=30, max=300)),
    vol.Optional("show_text", default=True): cv.boolean,
    vol.Optional("label"): cv.string,
    vol.Optional("label_size", default=24): vol.All(int, vol.Range(min=8, max=64)),
    vol.Optional("font"): cv.string,
    vol.Optional("feed", default=80): vol.All(int, vol.Range(min=0, max=255)),
})

PRINT_IMAGE_SCHEMA = vol.Schema({
    **TARGET_SCHEMA,
    **ATTENTE_SCHEMA,
    vol.Exclusive("path", "source"): cv.string,
    vol.Exclusive("url", "source"): cv.url,
    vol.Optional("threshold"): vol.All(int, vol.Range(min=0, max=255)),
    vol.Optional("invert", default=False): cv.boolean,
    vol.Optional("feed", default=80): vol.All(int, vol.Range(min=0, max=255)),
})

PRINT_WEATHER_SCHEMA = vol.Schema({
    **TARGET_SCHEMA,
    **ATTENTE_SCHEMA,
    vol.Required("weather_entity"): cv.entity_id,
    vol.Optional("days", default=4): vol.All(int, vol.Range(min=0, max=7)),
    vol.Optional("title", default="METEO"): cv.string,
    vol.Optional("size", default=36): vol.All(int, vol.Range(min=8, max=96)),
    vol.Optional("font"): cv.string,
    vol.Optional("bold", default=False): cv.boolean,
    vol.Optional("icons", default=True): cv.boolean,
    vol.Optional("feed", default=80): vol.All(int, vol.Range(min=0, max=255)),
})

CANCEL_SCHEMA = vol.Schema({**TARGET_SCHEMA})

FEED_SCHEMA = vol.Schema({
    **TARGET_SCHEMA,
    **ATTENTE_SCHEMA,
    vol.Optional("dots", default=80): vol.All(int, vol.Range(min=1, max=255)),
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configuré une imprimante.

    La mise en place n'exige pas que l'imprimante soit joignable : ces modèles
    s'endorment en quelques minutes et ne repondent qu'une fois reveilles. Une
    ConfigEntryNotReady laisserait l'entrée en échec permanent et n'aurait créé
    aucune entite. L'adresse est donc resolue au moment de chaque opération,
    avec un repli sur le nom annonce quand l'imprimante a change d'adresse.
    """
    printer = MiniPocketPrinter(
        hass,
        entry.data[CONF_ADDRESS],
        queue_limit=entry.options.get(CONF_QUEUE_LIMIT, DEFAULT_QUEUE_LIMIT),
        transport=entry.options.get(CONF_TRANSPORT, DEFAULT_TRANSPORT),
        usb_path=entry.options.get(CONF_USB_PATH) or None,
        job_ttl=entry.options.get(CONF_JOB_TTL, DEFAULT_JOB_TTL),
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = printer
    entry.async_on_unload(printer.surveiller_presence())

    keep_awake = entry.options.get(CONF_KEEP_AWAKE, DEFAULT_KEEP_AWAKE)
    if keep_awake:
        async def _touch(_now) -> None:
            try:
                await printer.async_touch()
            except Exception as err:  # noqa: BLE001 - best effort
                _LOGGER.debug("Interrogation periodique sans effet : %s", err)

        entry.async_on_unload(
            async_track_time_interval(
                hass, _touch, timedelta(minutes=keep_awake)
            )
        )
        # Première interrogation sans attendre l'échéance : sinon les valeurs
        # restent vides pendant tout le premier intervalle.
        hass.async_create_background_task(
            _touch(None), f"{entry.entry_id} premiere interrogation"
        )

    _async_cleanup_devices(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


@callback
def _async_cleanup_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Retire les appareils fantômes laisses par les anciennes versions.

    L'appareil etait autrefois identifié par son adresse Bluetooth. Chaque
    changement d'adresse en creait un nouveau et abandonnait le précédent,
    qui restait affiché avec une adresse morte. L'identifiant est desormais
    l'entrée de configuration ; on supprimé les orphelins au demarrage.
    """
    registry = dr.async_get(hass)
    current = (DOMAIN, entry.entry_id)

    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if current in device.identifiers:
            # Une seule connexion, en minuscules : les versions precedentes
            # declaraient la même adresse en majuscules et en minuscules,
            # que le registre affichait alors deux fois.
            address = entry.data[CONF_ADDRESS]
            expected = {(dr.CONNECTION_BLUETOOTH, address.lower())}
            if device.connections != expected:
                registry.async_update_device(device.id, new_connections=expected)
            continue

        _LOGGER.info(
            "Suppression de l'appareil obsolete %s (%s)",
            device.name, device.identifiers,
        )
        registry.async_update_device(
            device.id, remove_config_entry_id=entry.entry_id
        )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Autorise la suppression manuelle d'un appareil depuis l'interface."""
    return (DOMAIN, entry.entry_id) not in device.identifiers


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Recharge après modification des options.

    async_schedule_reload plutôt qu'async_reload : recharger depuis l'écouteur
    lui-même provoque un avertissement de Home Assistant, l'entrée étant
    encore en cours de mise à jour au moment de l'appel.
    """
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Retire une imprimante."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


def _resolve_all(hass: HomeAssistant, call: ServiceCall) -> list[MiniPocketPrinter]:
    """Toutes les imprimantes visees par l'appel.

    Une action peut cibler plusieurs appareils : on imprime sur chacun, au
    lieu de retenir seulement le premier.
    """
    printers: dict[str, MiniPocketPrinter] = hass.data.get(DOMAIN, {})
    if not printers:
        raise HomeAssistantError("Aucune imprimante configuree")

    selected: list[MiniPocketPrinter] = []
    registry = dr.async_get(hass)

    for device_id in call.data.get(ATTR_DEVICE_ID) or []:
        device = registry.async_get(device_id)
        if device is None:
            raise HomeAssistantError(f"Appareil inconnu : {device_id}")
        matched = [
            printers[entry_id]
            for entry_id in device.config_entries
            if entry_id in printers
        ]
        if not matched:
            raise HomeAssistantError(
                f"L'appareil {device.name} n'est pas une imprimante geree"
            )
        selected.extend(p for p in matched if p not in selected)

    if entry_id := call.data.get("entry_id"):
        if entry_id not in printers:
            raise HomeAssistantError(f"Imprimante inconnue : {entry_id}")
        printer = printers[entry_id]
        if printer not in selected:
            selected.append(printer)

    if selected:
        return selected

    if len(printers) == 1:
        return [next(iter(printers.values()))]

    names = ", ".join(
        entry.title
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id in printers
    )
    raise HomeAssistantError(
        f"Plusieurs imprimantes configurees ({names}) : precisez la cible"
    )


async def _print_on_all(hass: HomeAssistant, call: ServiceCall,
                        rows: list[bytes], feed: int) -> None:
    """Imprime le même document sur chaque imprimante ciblée.

    Rend la main immédiatement, sauf si l'appel demande à attendre : un ticket
    prend plusieurs secondes et une automatisation n'a pas à rester suspendue.
    Les erreurs sont regroupées, une imprimante éteinte n'empêchant pas les
    autres d'imprimer.
    """
    printers = _resolve_all(hass, call)

    async def _travail() -> list[str]:
        echecs: list[str] = []
        for printer in printers:
            try:
                if not await printer.async_print(rows, feed=feed):
                    echecs.append(f"{printer.address} : fin non confirmée")
            except QueueFull as err:
                echecs.append(str(err))
            except Exception as err:  # noqa: BLE001 - rapporté à l'appelant
                echecs.append(f"{printer.address} : {err}")
        return echecs

    if not call.data.get("wait"):
        async def _en_fond() -> None:
            for message in await _travail():
                _LOGGER.warning("Impression : %s", message)


        hass.async_create_background_task(
            _en_fond(), f"{DOMAIN} impression {call.service}"
        )
        return

    echecs = await _travail()
    if echecs:
        raise HomeAssistantError(" | ".join(echecs))


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_PRINT_TEXT):
        return

    async def handle_print_text(call: ServiceCall) -> None:
        rows = await hass.async_add_executor_job(
            lambda: text_to_rows(
                call.data["text"],
                size=call.data["size"],
                font_path=call.data.get("font"),
                spacing=call.data["spacing"],
                align=call.data["align"],
                bold=call.data["bold"],
            )
        )
        await _print_on_all(hass, call, rows, call.data["feed"])

    async def handle_print_table(call: ServiceCall) -> None:
        rows_data = [
            ["" if cell is None else str(cell) for cell in row]
            for row in call.data["rows"]
        ]
        try:
            rows = await hass.async_add_executor_job(
                lambda: build_table_rows(
                    rows_data,
                    headers=call.data.get("headers"),
                    title=call.data.get("title"),
                    align=call.data.get("align"),
                    size=call.data["size"],
                    font_path=call.data.get("font"),
                    bold=call.data["bold"],
                    grid=call.data["grid"],
                )
            )
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await _print_on_all(hass, call, rows, call.data["feed"])

    async def handle_print_qr(call: ServiceCall) -> None:
        rows = await hass.async_add_executor_job(
            lambda: build_qr_rows(
                call.data["data"],
                scale=call.data["scale"],
                border=call.data["border"],
                label=call.data.get("label"),
                label_size=call.data["label_size"],
                font_path=call.data.get("font"),
                error_correction=call.data["error_correction"],
            )
        )
        await _print_on_all(hass, call, rows, call.data["feed"])

    async def handle_print_barcode(call: ServiceCall) -> None:
        try:
            rows = await hass.async_add_executor_job(
                lambda: build_barcode_rows(
                    call.data["data"],
                    symbology=call.data["symbology"],
                    height=call.data["height"],
                    label=call.data.get("label"),
                    label_size=call.data["label_size"],
                    font_path=call.data.get("font"),
                    show_text=call.data["show_text"],
                )
            )
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await _print_on_all(hass, call, rows, call.data["feed"])

    async def handle_print_image(call: ServiceCall) -> None:

        if url := call.data.get("url"):
            session = async_get_clientsession(hass)
            async with session.get(url, timeout=ClientTimeout(total=30)) as response:
                response.raise_for_status()
                data = await response.read()
        elif path := call.data.get("path"):
            if not hass.config.is_allowed_path(path):
                raise HomeAssistantError(f"Chemin non autorise : {path}")
            data = await hass.async_add_executor_job(
                lambda: open(path, "rb").read()  # noqa: SIM115
            )
        else:
            raise HomeAssistantError("Indiquez 'path' ou 'url'")

        try:
            rows = await hass.async_add_executor_job(
                lambda: image_bytes_to_rows(
                    data, call.data.get("threshold"), call.data["invert"]
                )
            )
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await _print_on_all(hass, call, rows, call.data["feed"])

    async def handle_print_weather(call: ServiceCall) -> None:
        size = call.data["size"]
        font_path = call.data.get("font")

        if call.data["icons"]:
            data = await async_collect_weather(
                hass, call.data["weather_entity"],
                days=call.data["days"], title=call.data["title"],
            )
            rows = await hass.async_add_executor_job(
                lambda: build_weather_rows(
                    data, size=size, font_path=font_path,
                    bold=call.data["bold"], days=call.data["days"],
                )
            )
        else:
            # Version tout texte : les colonnes sont mesurees sur la police.
            font = await hass.async_add_executor_job(load_font, size, font_path)
            width = await hass.async_add_executor_job(columns_for, font)
            ticket = await async_build_weather_ticket(
                hass, call.data["weather_entity"],
                days=call.data["days"], title=call.data["title"],
                line_width=width,
            )
            rows = await hass.async_add_executor_job(
                lambda: text_to_rows(ticket, size=size, font_path=font_path,
                                     spacing=4, bold=call.data["bold"])
            )

        await _print_on_all(hass, call, rows, call.data["feed"])

    async def handle_cancel(call: ServiceCall) -> None:
        total = sum(printer.cancel_queued() for printer in _resolve_all(hass, call))
        _LOGGER.info("%d travail(s) en attente annule(s)", total)

    async def handle_feed(call: ServiceCall) -> None:
        printers = _resolve_all(hass, call)
        dots = call.data["dots"]

        async def _travail() -> list[str]:
            echecs = []
            for printer in printers:
                try:
                    await printer.async_feed(dots)
                except Exception as err:  # noqa: BLE001
                    echecs.append(f"{printer.address} : {err}")
            return echecs

        if not call.data.get("wait"):
            async def _en_fond() -> None:
                for message in await _travail():
                    _LOGGER.warning("Avance papier : %s", message)

            hass.async_create_background_task(
                _en_fond(), f"{DOMAIN} avance papier"
            )
            return

        echecs = await _travail()
        if echecs:
            raise HomeAssistantError(" | ".join(echecs))

    hass.services.async_register(
        DOMAIN, SERVICE_PRINT_TEXT, handle_print_text, schema=PRINT_TEXT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PRINT_TABLE, handle_print_table, schema=PRINT_TABLE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PRINT_QR, handle_print_qr, schema=PRINT_QR_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PRINT_BARCODE, handle_print_barcode,
        schema=PRINT_BARCODE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PRINT_IMAGE, handle_print_image, schema=PRINT_IMAGE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PRINT_WEATHER, handle_print_weather,
        schema=PRINT_WEATHER_SCHEMA,
    )
    hass.services.async_register(DOMAIN, SERVICE_FEED, handle_feed, schema=FEED_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL, handle_cancel, schema=CANCEL_SCHEMA
    )
