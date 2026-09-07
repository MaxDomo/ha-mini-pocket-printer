"""Pictogrammes meteo dessines à la volee.

Pas de fichiers d'images a embarquer : tout est trace geometriquement, en noir
sur blanc, ce qui donne des contours nets sur une tête thermique et permet
n'importe quelle taille sans perte.
"""

from __future__ import annotations

from math import cos, pi, sin

from PIL import ImageDraw


def _sun(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, w: int) -> None:
    r = size * 0.26
    cx, cy = x + size / 2, y + size / 2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=w, fill=255)
    for i in range(8):
        angle = i * pi / 4
        x1, y1 = cx + cos(angle) * r * 1.45, cy + sin(angle) * r * 1.45
        x2, y2 = cx + cos(angle) * r * 2.0, cy + sin(angle) * r * 2.0
        draw.line([x1, y1, x2, y2], fill=0, width=w)


def _moon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, w: int) -> None:
    r = size * 0.3
    cx, cy = x + size / 2, y + size / 2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=w)
    # masque decale pour creuser le croissant
    draw.ellipse([cx - r * 0.55, cy - r * 1.15, cx + r * 1.5, cy + r * 0.85],
                 fill=255, outline=255)


def _cloud(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, w: int,
           scale: float = 1.0, dy: float = 0.0) -> None:
    s = size * scale
    left = x + (size - s) / 2
    top = y + size * 0.28 + dy
    r1 = s * 0.20
    r2 = s * 0.26
    r3 = s * 0.17
    draw.ellipse([left + s * 0.05, top + s * 0.12,
                  left + s * 0.05 + 2 * r1, top + s * 0.12 + 2 * r1],
                 outline=0, width=w, fill=255)
    draw.ellipse([left + s * 0.28, top - r2 * 0.2,
                  left + s * 0.28 + 2 * r2, top - r2 * 0.2 + 2 * r2],
                 outline=0, width=w, fill=255)
    draw.ellipse([left + s * 0.60, top + s * 0.16,
                  left + s * 0.60 + 2 * r3, top + s * 0.16 + 2 * r3],
                 outline=0, width=w, fill=255)
    draw.rectangle([left + s * 0.13, top + s * 0.34,
                    left + s * 0.80, top + s * 0.52], fill=255, outline=255)
    draw.line([left + s * 0.13, top + s * 0.52,
               left + s * 0.80, top + s * 0.52], fill=0, width=w)


def _drops(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, w: int,
           count: int = 3, length: float = 0.14) -> None:
    top = y + size * 0.72
    for i in range(count):
        px = x + size * (0.28 + i * 0.18)
        draw.line([px, top, px - size * 0.05, top + size * length],
                  fill=0, width=w)


def _flakes(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, w: int) -> None:
    top = y + size * 0.78
    for i in range(3):
        px = x + size * (0.30 + i * 0.18)
        r = size * 0.05
        draw.line([px - r, top, px + r, top], fill=0, width=w)
        draw.line([px, top - r, px, top + r], fill=0, width=w)


def _bolt(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, w: int) -> None:
    top = y + size * 0.66
    draw.polygon([
        (x + size * 0.52, top),
        (x + size * 0.36, top + size * 0.20),
        (x + size * 0.48, top + size * 0.20),
        (x + size * 0.38, top + size * 0.34),
        (x + size * 0.62, top + size * 0.14),
        (x + size * 0.48, top + size * 0.14),
    ], fill=0)


def _fog(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, w: int) -> None:
    for i, frac in enumerate((0.38, 0.52, 0.66, 0.80)):
        inset = size * (0.12 if i % 2 == 0 else 0.22)
        draw.line([x + inset, y + size * frac,
                   x + size - inset, y + size * frac], fill=0, width=w)


def _wind(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, w: int) -> None:
    for i, frac in enumerate((0.40, 0.55, 0.70)):
        end = size * (0.72 if i != 1 else 0.82)
        draw.line([x + size * 0.15, y + size * frac,
                   x + end, y + size * frac], fill=0, width=w)
        draw.arc([x + end - size * 0.12, y + size * (frac - 0.10),
                  x + end + size * 0.10, y + size * (frac + 0.10)],
                 start=270, end=110, fill=0, width=w)


def _bang(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, w: int) -> None:
    cx = x + size / 2
    draw.line([cx, y + size * 0.28, cx, y + size * 0.62], fill=0, width=w * 2)
    draw.ellipse([cx - w, y + size * 0.70, cx + w, y + size * 0.70 + 2 * w],
                 fill=0)


DRAWERS = {
    "sunny": lambda d, x, y, s, w: _sun(d, x, y, s, w),
    "clear-night": lambda d, x, y, s, w: _moon(d, x, y, s, w),
    "cloudy": lambda d, x, y, s, w: _cloud(d, x, y, s, w, 0.95),
    "partlycloudy": lambda d, x, y, s, w: (
        # soleil en haut a gauche, nuage par-dessus en bas a droite
        _sun(d, x + s * 0.02, y - s * 0.14, s * 0.52, w),
        _cloud(d, x + s * 0.08, y + s * 0.14, s * 0.92, w, 0.92),
    ),
    "rainy": lambda d, x, y, s, w: (_cloud(d, x, y, s, w, 0.85),
                                    _drops(d, x, y, s, w)),
    "pouring": lambda d, x, y, s, w: (_cloud(d, x, y, s, w, 0.85),
                                      _drops(d, x, y, s, w, 4, 0.20)),
    "snowy": lambda d, x, y, s, w: (_cloud(d, x, y, s, w, 0.85),
                                    _flakes(d, x, y, s, w)),
    "snowy-rainy": lambda d, x, y, s, w: (_cloud(d, x, y, s, w, 0.85),
                                          _drops(d, x, y, s, w, 2),
                                          _flakes(d, x + s * 0.20, y, s, w)),
    "hail": lambda d, x, y, s, w: (_cloud(d, x, y, s, w, 0.85),
                                   _flakes(d, x, y, s, w)),
    "lightning": lambda d, x, y, s, w: (_cloud(d, x, y, s, w, 0.85),
                                        _bolt(d, x, y, s, w)),
    "lightning-rainy": lambda d, x, y, s, w: (_cloud(d, x, y, s, w, 0.85),
                                              _bolt(d, x, y, s, w),
                                              _drops(d, x - s * 0.12, y, s, w, 2)),
    "fog": lambda d, x, y, s, w: _fog(d, x, y, s, w),
    "windy": lambda d, x, y, s, w: _wind(d, x, y, s, w),
    "windy-variant": lambda d, x, y, s, w: _wind(d, x, y, s, w),
    "exceptional": lambda d, x, y, s, w: _bang(d, x, y, s, w),
}


def draw_condition(draw: ImageDraw.ImageDraw, condition: str | None,
                   x: int, y: int, size: int) -> None:
    """Trace le pictogramme d'une condition dans un carre de côté `size`."""
    width = max(2, round(size / 20))
    drawer = DRAWERS.get(condition or "", DRAWERS["cloudy"])
    drawer(draw, x, y, size, width)
