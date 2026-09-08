"""Diagnostic téléchargeable depuis la fiche de l'appareil.

Rassemble en un seul JSON tout ce qu'il faut pour comprendre un échec :
transport retenu, dernière erreur, état d'appairage, points d'écoute et
valeurs lues. Évite de réclamer dix captures de journal.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from . import usb
from .addresses import classic_address
from .const import DOMAIN
from .printer import MiniPocketPrinter

# L'adresse n'est pas un secret en soi, mais elle identifie un domicile :
# on la tronque comme le fait Home Assistant pour ses propres diagnostics.
A_MASQUER = {CONF_ADDRESS, "name"}


def _masque(adresse: str | None) -> str | None:
    if not adresse or len(adresse) < 8:
        return adresse
    return adresse[:8] + ":**:**:**"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    printer: MiniPocketPrinter = hass.data[DOMAIN][entry.entry_id]

    return {
        "entree": {
            "options": dict(entry.options),
            "version": entry.version,
        },
        "transport": {
            "configure": printer.transport,
            "actif": printer.active_transport,
            "usb_configure": bool(printer.usb_path),
            "peripheriques_usb": [d["path"] for d in usb.list_devices()],
        },
        "liaison": {
            "adresse_ble": _masque(printer.address),
            "joignable": printer.available,
            "notifications_refusees": printer.notify_refused,
            "signal_dbm": printer.rssi,
            "points_ecoute": [
                {"source": point["source"], "rssi": point["rssi"],
                 "connectable": point["connectable"]}
                for point in printer.scanners
            ],
        },
        "imprimante": {
            "modele": printer.model,
            "firmware": printer.firmware,
            "numero_de_serie": printer.serial,
            "batterie": printer.battery,
            "densite": printer.density,
            "veille_minutes": printer.sleep_minutes,
            "statut_brut": None if printer.status_raw is None
            else f"0x{printer.status_raw:02x}",
            "papier_ok": printer.paper_ok,
        },
        "activite": {
            "occupee": printer.busy,
            "en_attente": printer.queued,
            "limite_file": printer.queue_limit,
            "derniere_impression": printer.last_print.isoformat()
            if printer.last_print else None,
            "derniere_erreur": printer.last_error,
        },
    }
