"""Capteur de batterie de l'imprimante."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ADDRESS,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, MANUFACTURER, MODEL
from .printer import MiniPocketPrinter

_LOGGER = logging.getLogger(__name__)

# Chaque relevé ouvre une connexion, et l'imprimante n'en accepté qu'une :
# on espace largement, la valeur est aussi rafraichie après chaque impression.
SCAN_INTERVAL = timedelta(hours=1)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    printer = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PrinterLastPrintSensor(entry, printer),
        PrinterRssiSensor(entry, printer),
        PrinterQueueSensor(entry, printer),
    ])
    # Pas de update_before_add : réveiller une imprimante endormie prend
    # plusieurs dizaines de secondes et bloquerait la mise en place de
    # la plateforme. Le premier relevé est lance en arriere-plan.
    async_add_entities([PrinterBatterySensor(entry, printer)])


class PrinterBatterySensor(SensorEntity):
    """Pourcentage renvoye par la commande 10 FF 50 F1."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Batterie"
    # Le relevé ouvre une connexion BLE qui peut durer : hors du cycle de
    # scrutation des entites, sinon Home Assistant avertit a 10 secondes.
    _attr_should_poll = False

    @property
    def native_value(self) -> int | None:
        return self._printer.battery

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        self._printer = printer
        self._reported = False
        self._retry_after = None
        self._last_attempt = 0.0
        self._address = entry.data[CONF_ADDRESS]
        # Identifiant base sur l'entrée, pas sur l'adresse : celle-ci change a
        # chaque allumage et emporterait l'appareil et l'entite avec elle.
        self._attr_unique_id = f"{entry.entry_id}_battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(CONNECTION_BLUETOOTH, self._address.lower())},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """Programme les relevés en arriere-plan."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._printer.add_listener(self.async_write_ha_state)
        )
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_refresh, SCAN_INTERVAL)
        )

        @callback
        def _reappeared(_info, _change) -> None:
            """Tente un relevé des que l'imprimante se remet a annoncer.

            Elle dort la plupart du temps : attendre l'echeance horaire
            revient à viser une fenêtre qui n'existe presque jamais.
            """
            if self._printer.battery is not None:
                return
            # Les annonces arrivent plusieurs fois par seconde : on ne
            # retente pas plus d'une fois toutes les cinq minutes.
            now = self.hass.loop.time()
            if now - self._last_attempt < 300:
                return
            self._last_attempt = now
            self.hass.async_create_background_task(
                self._async_refresh(), f"{self.entity_id} releve a l'apparition"
            )

        self.async_on_remove(
            bluetooth.async_register_callback(
                self.hass,
                _reappeared,
                {"address": self._printer.address, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )

        self.hass.async_create_background_task(
            self._async_refresh(), f"{self.entity_id} premier releve batterie"
        )

    async def _async_refresh(self, _now=None) -> None:
        await self.async_update()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        # Après un échec, on laisse l'imprimante tranquille : la relancer
        # toutes les minutes ne fait qu'occuper la radio et remplir le journal.
        loop = asyncio.get_running_loop()
        if self._retry_after and loop.time() < self._retry_after:
            return

        try:
            async with asyncio.timeout(45):
                await self._printer.async_update()
        except Exception as err:  # noqa: BLE001 - endormie, occupée, hors de portée
            # Premier échec visible dans le journal, les suivants en debug :
            # l'imprimante passe l'essentiel de son temps éteinte.
            if self._reported:
                _LOGGER.debug("Releve batterie impossible pour %s : %s",
                              self._address, err)
            else:
                _LOGGER.warning("Releve batterie impossible pour %s : %s",
                                self._address, err)
                self._reported = True
            # Pas de mesure, pas de valeur affichée.
            self._printer.battery = None
            self._retry_after = loop.time() + 900
            self._attr_available = self._printer.available
            return
        self._reported = False
        self._retry_after = None
        self._last_attempt = 0.0
        self._attr_available = True
        if self._printer.firmware:
            self._attr_device_info["sw_version"] = self._printer.firmware
        # Le modèle affiché reste celui du matériel (Tronic 5890) ; la valeur
        # renvoyee par 10 FF 20 F0 est un identifiant interne, expose en
        # attribut plutot qu'en modèle.
        if self._printer.model:
            self._attr_device_info["model_id"] = self._printer.model
        if self._printer.serial:
            self._attr_device_info["serial_number"] = self._printer.serial


class _PrinterEntity(SensorEntity):
    """Base commune : rattachement à l'appareil, pas de scrutation."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter,
                 key: str, name: str) -> None:
        self._printer = printer
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
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

        # Rafraichissement immediat des qu'une lecture aboutit.
        self.async_on_remove(
            self._printer.add_listener(self.async_write_ha_state)
        )

        # Le rappel doit porter @callback : sans lui, Home Assistant execute
        # la fonction dans un thread d'executeur, et async_write_ha_state n'est
        # pas sur en dehors de la boucle d'evenements.
        @callback
        def _refresh(_now) -> None:
            self.async_write_ha_state()

        # Rafraichi toutes les minutes pour suivre la disponibilité, sans
        # jamais ouvrir de connexion.
        self.async_on_remove(
            async_track_time_interval(self.hass, _refresh, timedelta(seconds=60))
        )


class PrinterLastPrintSensor(_PrinterEntity):
    """Horodatage de la dernière impression réussie."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:receipt-text-clock"

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        super().__init__(entry, printer, "last_print", "Derniere impression")
        self._attr_translation_key = "last_print"

    @property
    def native_value(self):
        return self._printer.last_print


class PrinterRssiSensor(_PrinterEntity):
    """Puissance du signal telle que vue par Home Assistant.

    Indicateur de placement : au-dessus de -80 dBm la connexion tient, en
    dessous de -90 elle devient aleatoire. Déduit des annonces, donc gratuit.
    """

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_suggested_display_precision = 0

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        super().__init__(entry, printer, "rssi", "Signal")
        self._attr_translation_key = "rssi"

    @property
    def native_value(self) -> int | None:
        return self._printer.rssi

    @property
    def extra_state_attributes(self) -> dict:
        """Detail par point d'ecoute : adaptateurs locaux et proxys.

        Home Assistant se connecte via celui qui entend le mieux : ce detail
        indique lequel sera choisi, et aide à placer un proxy.
        """
        scanners = self._printer.scanners
        return {
            "meilleur_point": scanners[0]["source"] if scanners else None,
            "points_ecoute": [
                {
                    "source": item["source"],
                    "rssi": item["rssi"],
                    "connectable": item["connectable"],
                }
                for item in scanners
            ],
            "nombre": len(scanners),
        }

    @property
    def available(self) -> bool:
        return self._printer.rssi is not None


class PrinterQueueSensor(_PrinterEntity):
    """Nombre de travaux pris en charge : celui en cours et ceux en attente.

    Mise à jour evenementielle : l'imprimante previent à chaque mouvement de
    file, sans attendre le cycle de rafraichissement.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "travaux"

    def __init__(self, entry: ConfigEntry, printer: MiniPocketPrinter) -> None:
        super().__init__(entry, printer, "queue", "File d'attente")
        self._attr_translation_key = "queue"

    @property
    def native_value(self) -> int:
        return self._printer.pending

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "en_cours": 1 if self._printer.busy else 0,
            "en_attente": self._printer.queued,
            "limite": self._printer.queue_limit or "illimitee",
        }
