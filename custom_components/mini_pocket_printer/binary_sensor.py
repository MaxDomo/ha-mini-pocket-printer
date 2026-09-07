"""Capteur de présence de papier."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, MODEL
from .printer import MiniPocketPrinter


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    printer = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PrinterPaperSensor(entry, printer)])


class PrinterPaperSensor(BinarySensorEntity):
    """Absence de papier : bit 0x04 de l'octet 10 FF 40.

    Vérifié sur l'appareil : 0x00 rouleau en place, 0x04 rouleau retire.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Papier"
    _attr_translation_key = "paper"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        self._printer = printer
        self._address = entry.data[CONF_ADDRESS]
        self._attr_unique_id = f"{entry.entry_id}_paper"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(CONNECTION_BLUETOOTH, self._address.lower())},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def available(self) -> bool:
        return self._printer.status_raw is not None

    @property
    def is_on(self) -> bool | None:
        ok = self._printer.paper_ok
        return None if ok is None else not ok

    @property
    def extra_state_attributes(self) -> dict:
        raw = self._printer.status_raw
        return {
            "octet_statut": None if raw is None else f"0x{raw:02x}",
            "bits": None if raw is None else format(raw, "08b"),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._printer.add_listener(self.async_write_ha_state)
        )
