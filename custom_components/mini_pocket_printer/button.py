"""Boutons : ticket de diagnostic, annulation de file, relecture."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER, MODEL
from .printer import DENSITY_LEVELS, MiniPocketPrinter
from .table_render import build_table_rows

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    printer = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PrinterTestButton(entry, printer),
        PrinterCancelButton(entry, printer),
        PrinterRefreshButton(entry, printer),
    ])


class PrinterTestButton(ButtonEntity):
    """Imprime un état des lieux : liaison, firmware, batterie, signal."""

    _attr_has_entity_name = True
    _attr_name = "Ticket de test"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:printer-check"

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        self._printer = printer
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_test"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(CONNECTION_BLUETOOTH,
                          entry.data[CONF_ADDRESS].lower())},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def available(self) -> bool:
        return self._printer.available

    async def async_press(self) -> None:
        # Relevé avant impression : sans cela le ticket reprend les valeurs
        # de la dernière lecture, parfois vieilles de plusieurs heures, et
        # affiché des points d'interrogation après un redemarrage.
        try:
            await self._printer.async_update()
        except Exception as err:  # noqa: BLE001 - le ticket sort quand même
            _LOGGER.warning("Releve avant ticket de test impossible : %s", err)

        rssi = self._printer.rssi
        scanners = self._printer.scanners
        rows = [
            ["Adresse", self._printer.address],
            ["Transport", self._printer.active_transport.upper()],
            ["Modele", f"{MANUFACTURER} {MODEL}"],
            ["Code interne", self._printer.model or "?"],
            ["Firmware", self._printer.firmware or "?"],
            ["Papier", "absent" if self._printer.paper_ok is False
                else "en place" if self._printer.paper_ok else "?"],
            ["Densite", DENSITY_LEVELS.get(self._printer.density, "?")],
            ["Veille", f"{self._printer.sleep_minutes} min"
                if self._printer.sleep_minutes is not None else "?"],
            ["Batterie", f"{self._printer.battery} %"
                if self._printer.battery is not None else "?"],
            ["Signal", f"{rssi} dBm" if rssi is not None else "?"],
            ["Via", scanners[0]["source"][:14] if scanners else "?"],
            ["Points ecoute", str(len(scanners))],
            ["Notifications", "non" if self._printer.notify_refused else "oui"],
            ["Date", dt_util.now().strftime("%d/%m/%Y %H:%M")],
        ]
        bitmap = await self.hass.async_add_executor_job(
            lambda: build_table_rows(
                rows, title="TICKET DE TEST", align=["left", "right"],
                size=28, grid=False,
            )
        )
        if not await self._printer.async_print(bitmap):
            raise HomeAssistantError("Pas de confirmation de fin d'impression")


class PrinterCancelButton(ButtonEntity):
    """Vide la file d'attente sans toucher au travail en cours."""

    _attr_has_entity_name = True
    _attr_name = "Annuler la file"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:playlist-remove"

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        self._printer = printer
        self._attr_unique_id = f"{entry.entry_id}_cancel"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(CONNECTION_BLUETOOTH,
                          entry.data[CONF_ADDRESS].lower())},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_press(self) -> None:
        self._printer.cancel_queued()


class PrinterRefreshButton(ButtonEntity):
    """Relit densité, veille, batterie et statut depuis l'imprimante."""

    _attr_has_entity_name = True
    _attr_name = "Relire les reglages"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:refresh"

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        self._printer = printer
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(CONNECTION_BLUETOOTH,
                          entry.data[CONF_ADDRESS].lower())},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def available(self) -> bool:
        return self._printer.available

    async def async_press(self) -> None:
        await self._printer.async_update()
