"""Flux de configuration : decouverte Bluetooth ou choix manuel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from .spp import classic_address
from .usb import list_devices
from .const import (
    CONF_KEEP_AWAKE,
    CONF_QUEUE_LIMIT,
    CONF_TRANSPORT,
    CONF_USB_PATH,
    DEFAULT_KEEP_AWAKE,
    DEFAULT_QUEUE_LIMIT,
    DEFAULT_TRANSPORT,
    TRANSPORTS,
    DOMAIN,
)


class MiniPocketPrinterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ajout d'une imprimante depuis l'interface."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}
        self._discovery: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """L'imprimante s'est annoncee et Home Assistant la propose."""
        # L'imprimante expose deux faces : BLE en 5E:55:... et classique en
        # 55:55:... Elles se resolvent vers la même adresse classique, qui
        # sert donc d'identifiant unique : une seule entrée pour l'appareil.
        await self.async_set_unique_id(classic_address(discovery_info.address))
        self._abort_if_unique_id_configured(
            updates={CONF_ADDRESS: discovery_info.address}
        )
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None
        if user_input is not None:
            suffix = self._discovery.address.replace(":", "")[-4:]
            return self.async_create_entry(
                title=f"{self._discovery.name or 'Mini Pocket Printer'} {suffix}",
                data={
                    CONF_ADDRESS: self._discovery.address,
                    CONF_NAME: self._discovery.name,
                },
                options={CONF_TRANSPORT: DEFAULT_TRANSPORT},
            )
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._discovery.name or self._discovery.address
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return MiniPocketPrinterOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ajout manuel : liste les périphériques BLE joignables."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(classic_address(address),
                                           raise_on_progress=False)
            self._abort_if_unique_id_configured()
            name = self._discovered.get(address, "Mini Pocket Printer")
            suffix = classic_address(address).replace(":", "")[-4:]
            return self.async_create_entry(
                title=f"{name} {suffix}",
                data={CONF_ADDRESS: address, CONF_NAME: name},
                options={CONF_TRANSPORT: DEFAULT_TRANSPORT},
            )

        # Les entrées sont identifiees par l'adresse classique : comparer
        # l'adresse annoncee telle quelle laisserait passer une imprimante
        # déjà configurée, ou masquerait une nouvelle.
        configured = {classic_address(a) for a in self._async_current_ids()}
        for info in async_discovered_service_info(self.hass, connectable=True):
            if classic_address(info.address) in configured:
                continue
            name = info.name or ""
            if "pocket printer" not in name.lower():
                continue
            # Une seule proposition par exemplaire, en preferant la face BLE :
            # l'intégration en déduit l'adresse classique, l'inverse aussi,
            # mais le BLE reste le point d'entrée naturel.
            key = classic_address(info.address)
            existing = {classic_address(a): a for a in self._discovered}
            if key in existing:
                if info.address.upper().startswith("5E"):
                    self._discovered.pop(existing[key], None)
                else:
                    continue
            self._discovered[info.address] = name

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(
                    {
                        address: f"{name} ({address})"
                        for address, name in self._discovered.items()
                    }
                )
            }),
        )


class MiniPocketPrinterOptionsFlow(OptionsFlow):
    """Réglages de l'intégration : transport, adaptateur, file, interrogation."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options

        # Périphériques USB detectes, plus celui déjà enregistré s'il n'est
        # pas branche : sinon la valeur disparaitrait du selecteur.
        devices = await self.hass.async_add_executor_job(list_devices)
        choices = {device["path"]: device["label"] for device in devices}
        current = options.get(CONF_USB_PATH, "")
        if current and current not in choices:
            choices[current] = f"{current} (non detecte)"
        choices = {"": "aucun"} | choices

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_TRANSPORT,
                    default=options.get(CONF_TRANSPORT, DEFAULT_TRANSPORT),
                ): vol.In(TRANSPORTS),
                vol.Optional(
                    CONF_USB_PATH,
                    description={"suggested_value": current},
                ): vol.In(choices),
                vol.Optional(
                    CONF_QUEUE_LIMIT,
                    default=options.get(CONF_QUEUE_LIMIT, DEFAULT_QUEUE_LIMIT),
                ): vol.All(int, vol.Range(min=0, max=50)),
                vol.Optional(
                    CONF_KEEP_AWAKE,
                    default=options.get(CONF_KEEP_AWAKE, DEFAULT_KEEP_AWAKE),
                ): vol.All(int, vol.Range(min=0, max=60)),
            }),
        )
