"""Logo Home Assistant pour l'en-tête du ticket de test.

Le logo officiel, converti une fois pour toutes en 1 bit dans
assets/homeassistant.png. Le redimensionnement se fait par facteur entier :
un rééchantillonnage quelconque hacherait les bords après seuillage.
"""

from __future__ import annotations

import os

from PIL import Image

from .printer import WIDTH_PX
from .text_render import pil_to_rows

CHEMIN = os.path.join(os.path.dirname(__file__), "assets", "homeassistant.png")


def logo_rows(hauteur: int = 96, marge: int = 6) -> list[bytes]:
    """Logo centré sur la largeur du papier, en lignes de 48 octets."""
    logo = Image.open(CHEMIN).convert("L")

    # Réduction par un facteur entier, puis seuil : les bords restent francs.
    facteur = max(1, round(logo.height / max(1, hauteur)))
    if facteur > 1:
        logo = logo.reduce(facteur)
    logo = logo.point(lambda p: 255 if p > 128 else 0, mode="1")

    total = logo.height + 2 * marge
    planche = Image.new("1", (WIDTH_PX, total), 1)
    planche.paste(logo, ((WIDTH_PX - logo.width) // 2, marge))
    return pil_to_rows(planche)
