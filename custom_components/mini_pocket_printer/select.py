"""Densité d'impression, réglage 10 FF 10 00 nn."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, MODEL
from .printer import DENSITY_LEVELS, MiniPocketPrinter

LABELS = list(DENSITY_LEVELS.values())


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    printer = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PrinterDensitySelect(entry, printer)])


class PrinterDensitySelect(SelectEntity):
    """Trois niveaux, comme dans l'application officielle."""

    _attr_has_entity_name = True
    _attr_translation_key = "density"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = LABELS
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        self._printer = printer
        self._attr_unique_id = f"{entry.entry_id}_density"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(CONNECTION_BLUETOOTH,
                          entry.data[CONF_ADDRESS].lower())},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._printer.add_listener(self.async_write_ha_state)
        )

        if self._printer.density is None:
            self.hass.async_create_background_task(
                self._async_first_read(), f"{self.entity_id} lecture reglages"
            )

    async def _async_first_read(self) -> None:
        try:
            await self._printer.async_read_settings()
        except Exception:  # noqa: BLE001 - imprimante endormie ou injoignable
            return
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._printer.available and self._printer.density is not None

    @property
    def current_option(self) -> str | None:
        return DENSITY_LEVELS.get(self._printer.density)

    async def async_select_option(self, option: str) -> None:
        level = LABELS.index(option)
        await self._printer.async_set_density(level)
        self.async_write_ha_state()
