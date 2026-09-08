"""Délai de mise en veille, réglage 10 FF 12 00 nn."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, UnitOfTime, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_JOB_TTL,
    CONF_KEEP_AWAKE,
    CONF_QUEUE_LIMIT,
    DEFAULT_JOB_TTL,
    DEFAULT_KEEP_AWAKE,
    DEFAULT_QUEUE_LIMIT,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .printer import MiniPocketPrinter


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    printer = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PrinterSleepNumber(entry, printer),
        PrinterQueueLimitNumber(entry),
        PrinterKeepAwakeNumber(entry),
        PrinterJobTtlNumber(entry),
    ])


class PrinterSleepNumber(NumberEntity):
    """Minutes avant mise en veille automatique."""

    _attr_has_entity_name = True
    _attr_translation_key = "sleep"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        self._printer = printer
        self._attr_unique_id = f"{entry.entry_id}_sleep"
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

        if self._printer.sleep_minutes is None:
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
        return (self._printer.available
                and self._printer.sleep_minutes is not None)

    @property
    def native_value(self) -> float | None:
        return self._printer.sleep_minutes

    async def async_set_native_value(self, value: float) -> None:
        await self._printer.async_set_sleep(int(value))
        self.async_write_ha_state()


class _OptionNumber(NumberEntity):
    """Réglage de l'intégration, edite depuis la fiche de l'appareil.

    Écrit dans les options de l'entrée, ce qui déclenché un rechargement :
    l'utilisateur n'a pas a chercher le menu Options.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, key: str, default: int) -> None:
        self._entry = entry
        self._key = key
        self._default = default
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(CONNECTION_BLUETOOTH,
                          entry.data[CONF_ADDRESS].lower())},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def native_value(self) -> float:
        return self._entry.options.get(self._key, self._default)

    async def async_set_native_value(self, value: float) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, self._key: int(value)},
        )


class PrinterQueueLimitNumber(_OptionNumber):
    """Travaux maximum en file, 0 pour illimite."""

    _attr_native_min_value = 0
    _attr_native_max_value = 50
    _attr_icon = "mdi:tray-full"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, CONF_QUEUE_LIMIT, DEFAULT_QUEUE_LIMIT)


class PrinterKeepAwakeNumber(_OptionNumber):
    """Intervalle des interrogations, en minutes. 0 les désactivé.

    Chaque interrogation relit batterie, papier et réglages, et repousse la
    mise en veille. C'est ce qui remplacé un cache : les valeurs affichees
    proviennent toujours d'une lecture récente.
    """

    _attr_native_min_value = 0
    _attr_native_max_value = 60
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:timer-sync-outline"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, CONF_KEEP_AWAKE, DEFAULT_KEEP_AWAKE)


class PrinterJobTtlNumber(_OptionNumber):
    """Heures au-delà desquelles un travail en attente est abandonné.

    Un ticket du matin qui sortirait en fin de journée n'intéresse personne :
    mieux vaut le laisser tomber. 0 conserve les travaux indéfiniment.
    """

    _attr_native_min_value = 0
    _attr_native_max_value = 48
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_icon = "mdi:timer-sand"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, CONF_JOB_TTL, DEFAULT_JOB_TTL)
