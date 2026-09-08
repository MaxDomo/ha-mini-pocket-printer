"""Adresses Bluetooth de l'imprimante.

Chaque exemplaire expose deux faces : BLE en 5E:55:... et Bluetooth classique
en 55:55:... Seul le premier octet change. L'adresse classique sert
d'identifiant d'entrée, pour qu'une imprimante découverte par l'une ou l'autre
face ne donne qu'une seule entrée.
"""

from __future__ import annotations

import re

FORME = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def normalize_address(address: str | None) -> str | None:
    """Met une adresse en forme, ou retourne None si elle est inexploitable."""
    if not address:
        return None
    propre = address.strip().replace("-", ":").replace(" ", "").upper()
    if len(propre) == 12 and ":" not in propre:
        propre = ":".join(propre[i:i + 2] for i in range(0, 12, 2))
    return propre if FORME.match(propre) else None


def classic_address(address: str) -> str:
    """Adresse classique correspondante, 5E:... devenant 55:..."""
    parts = address.upper().split(":")
    if len(parts) == 6 and parts[0] == "5E":
        return ":".join(["55"] + parts[1:])
    return address.upper()


def ble_address(address: str) -> str:
    """Adresse BLE correspondante, 55:... devenant 5E:..."""
    parts = address.upper().split(":")
    if len(parts) == 6 and parts[0] == "55":
        return ":".join(["5E"] + parts[1:])
    return address.upper()
