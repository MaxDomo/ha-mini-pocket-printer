"""Ticket meteo illustre : pictogrammes et texte composes dans une même image."""

from __future__ import annotations

from PIL import Image, ImageDraw

from .printer import WIDTH_PX
from .text_render import load_font, pil_to_rows
from .weather_icons import draw_condition

MARGIN = 8


def build_weather_rows(data: dict, size: int = 32, font_path: str | None = None,
                       bold: bool = False, days: int = 4) -> list[bytes]:
    """Compose le ticket et retourne des lignes de 48 octets.

    `data` provient de async_collect_weather : toutes ses chaines sont déjà
    reduites a de l'ASCII imprimable.
    """
    font = load_font(size, font_path)
    small = load_font(max(14, round(size * 0.85)), font_path)
    big = load_font(round(size * 1.9), font_path)
    stroke = 1 if bold else 0

    forecast = data.get("forecast", [])[:days]
    icon = round(WIDTH_PX * 0.30)
    row_h = round(size * 1.5)
    line_h = round(size * 1.35)

    height = MARGIN
    height += line_h * 2                      # titre + separateur
    height += line_h                          # nom de l'entite
    height += icon + round(size * 0.4)        # pictogramme et temperature
    height += line_h * len(data.get("details", []))
    if forecast:
        height += line_h * 2                  # separateur + intitule
        height += row_h * len(forecast)
    height += line_h * 2                      # separateur + horodatage
    height += MARGIN

    img = Image.new("L", (WIDTH_PX, height), 255)
    draw = ImageDraw.Draw(img)
    y = MARGIN

    def fitted(text: str, f, limit: int) -> str:
        """Raccourcit au caractère pres pour tenir dans la largeur donnée.

        Un nom d'entite comme 'OpenWeatherMap Saint-Germain-en-Laye' deborde
        des 384 px : on coupe et on suffixe par des points de suspension.
        """
        if draw.textbbox((0, 0), text, font=f, stroke_width=stroke)[2] <= limit:
            return text
        while text and draw.textbbox(
            (0, 0), text + "...", font=f, stroke_width=stroke
        )[2] > limit:
            text = text[:-1]
        return text.rstrip() + "..."

    def centered(text: str, f, dy: int) -> int:
        text = fitted(text, f, WIDTH_PX - 2 * MARGIN)
        w = draw.textbbox((0, 0), text, font=f, stroke_width=stroke)[2]
        draw.text(((WIDTH_PX - w) // 2, dy), text, font=f, fill=0,
                  stroke_width=stroke, stroke_fill=0)
        return dy

    centered(data["title"], font, y)
    y += line_h
    draw.line([MARGIN, y, WIDTH_PX - MARGIN, y], fill=0, width=2)
    y += round(line_h * 0.4)

    centered(data["name"], small, y)
    y += line_h

    # pictogramme a gauche, temperature a droite
    draw_condition(draw, data["condition"], MARGIN + 4, y, icon)
    temp = f"{data['temperature']}{data['unit']}"
    tw = draw.textbbox((0, 0), temp, font=big, stroke_width=stroke)[2]
    draw.text((WIDTH_PX - MARGIN - tw, y + icon * 0.18), temp, font=big, fill=0,
              stroke_width=stroke, stroke_fill=0)
    label_y = y + icon * 0.18 + round(size * 1.9)
    label = fitted(data["condition_label"], small,
                   WIDTH_PX - 2 * MARGIN - icon - 8)
    lw = draw.textbbox((0, 0), label, font=small)[2]
    draw.text((WIDTH_PX - MARGIN - lw, label_y), label, font=small, fill=0)
    y += icon + round(size * 0.4)

    for label, value in data.get("details", []):
        vw = draw.textbbox((0, 0), value, font=small)[2]
        label = fitted(label, small, WIDTH_PX - 2 * MARGIN - vw - 12)
        draw.text((MARGIN, y), label, font=small, fill=0)
        draw.text((WIDTH_PX - MARGIN - vw, y), value, font=small, fill=0)
        y += line_h

    if forecast:
        y += round(line_h * 0.3)
        draw.line([MARGIN, y, WIDTH_PX - MARGIN, y], fill=0, width=2)
        y += round(line_h * 0.4)
        draw.text((MARGIN, y), "PREVISIONS", font=small, fill=0)
        y += line_h

        for entry in forecast:
            draw.text((MARGIN, y + row_h * 0.2), entry["day"], font=font, fill=0,
                      stroke_width=stroke, stroke_fill=0)
            draw_condition(draw, entry["condition"],
                           MARGIN + round(size * 2.6), y, row_h)
            temps = f"{entry['high']}/{entry['low']}"
            tw = draw.textbbox((0, 0), temps, font=font, stroke_width=stroke)[2]
            draw.text((WIDTH_PX - MARGIN - tw, y + row_h * 0.2), temps,
                      font=font, fill=0, stroke_width=stroke, stroke_fill=0)
            y += row_h

    y += round(line_h * 0.3)
    draw.line([MARGIN, y, WIDTH_PX - MARGIN, y], fill=0, width=2)
    y += round(line_h * 0.4)
    centered(data["timestamp"], small, y)

    # La hauteur estimee est volontairement large : on rogne le blanc final
    # pour ne pas gaspiller de papier.
    bbox = img.point(lambda p: 0 if p > 128 else 255, mode="1").getbbox()
    if bbox:
        img = img.crop((0, 0, WIDTH_PX, min(height, bbox[3] + MARGIN)))

    return pil_to_rows(img.point(lambda p: 255 if p > 128 else 0, mode="1"))
