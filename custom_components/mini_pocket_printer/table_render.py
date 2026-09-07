"""Rendu de tableaux : colonnes mesurees, cellules repliees, filets optionnels."""

from __future__ import annotations

from PIL import Image, ImageDraw

from .printer import WIDTH_PX
from .text_render import load_font, pil_to_rows

MARGIN = 8
CELL_PAD = 6


def _wrap_cell(draw, text: str, font, width: int, stroke: int) -> list[str]:
    """Replie une cellule sur la largeur de sa colonne, au mot puis au caractère."""
    def measure(value: str) -> int:
        return draw.textbbox((0, 0), value, font=font, stroke_width=stroke)[2]

    if measure(text) <= width:
        return [text]

    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        trial = f"{current} {word}".strip()
        if current and measure(trial) > width:
            lines.append(current)
            current = word
        else:
            current = trial
        while measure(current) > width and len(current) > 1:
            cut = len(current)
            while cut > 1 and measure(current[:cut]) > width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    return lines or [""]


def build_table_rows(rows: list[list[str]], headers: list[str] | None = None,
                     title: str | None = None, align: list[str] | None = None,
                     size: int = 32, font_path: str | None = None,
                     bold: bool = False, grid: bool = True) -> list[bytes]:
    """Compose un tableau et retourne des lignes de 48 octets.

    Les largeurs de colonnes sont déduites du contenu reel, puis ramenees a la
    largeur du papier : une colonne courte ne gaspille pas de place, et une
    colonne longue est repliee plutot que tronquee.
    """
    font = load_font(size, font_path)
    head_font = load_font(size, font_path)
    title_font = load_font(round(size * 1.25), font_path)
    stroke = 1 if bold else 0

    table = [[str(cell) for cell in row] for row in rows]
    if headers:
        headers = [str(h) for h in headers]
        columns = max(len(headers), max((len(r) for r in table), default=0))
    else:
        columns = max((len(r) for r in table), default=0)
    if not columns:
        raise ValueError("Tableau vide")

    # normalise le nombre de cellules par ligne
    table = [row + [""] * (columns - len(row)) for row in table]
    if headers:
        headers = headers + [""] * (columns - len(headers))
    align = (align or []) + ["left"] * (columns - len(align or []))

    probe = ImageDraw.Draw(Image.new("L", (1, 1)))

    def measure(value: str, f) -> int:
        return probe.textbbox((0, 0), value, font=f, stroke_width=stroke)[2]

    # largeur naturelle de chaque colonne, puis repartition proportionnelle
    natural = []
    for index in range(columns):
        widest = max(
            [measure(row[index], font) for row in table]
            + ([measure(headers[index], head_font)] if headers else [0])
        )
        natural.append(max(widest, 1))

    usable = WIDTH_PX - 2 * MARGIN - CELL_PAD * (columns - 1)
    total = sum(natural)
    if total <= usable:
        widths = natural
        # la dernière colonne absorbe l'espace restant
        widths[-1] += usable - total
    else:
        widths = [max(20, round(n * usable / total)) for n in natural]

    # replie chaque cellule et calcule la hauteur de chaque ligne
    line_h = round(size * 1.25)
    wrapped_rows = []
    for row in table:
        cells = [
            _wrap_cell(probe, row[i], font, widths[i], stroke)
            for i in range(columns)
        ]
        wrapped_rows.append(cells)

    wrapped_head = None
    if headers:
        wrapped_head = [
            _wrap_cell(probe, headers[i], head_font, widths[i], stroke)
            for i in range(columns)
        ]

    height = MARGIN
    if title:
        height += round(size * 1.6)
    if wrapped_head:
        height += line_h * max(len(c) for c in wrapped_head) + 10
    for cells in wrapped_rows:
        height += line_h * max(len(c) for c in cells) + (6 if grid else 2)
    height += MARGIN

    img = Image.new("L", (WIDTH_PX, height), 255)
    draw = ImageDraw.Draw(img)
    y = MARGIN

    if title:
        width = measure(title, title_font)
        draw.text(((WIDTH_PX - width) // 2, y), title, font=title_font, fill=0,
                  stroke_width=stroke, stroke_fill=0)
        y += round(size * 1.6)

    def draw_cells(cells: list[list[str]], f, bold_row: bool) -> int:
        nonlocal y
        top = y
        x = MARGIN
        for index, lines in enumerate(cells):
            for offset, line in enumerate(lines):
                text_w = measure(line, f)
                if align[index] == "right":
                    px = x + widths[index] - text_w
                elif align[index] == "center":
                    px = x + (widths[index] - text_w) // 2
                else:
                    px = x
                draw.text((px, top + offset * line_h), line, font=f, fill=0,
                          stroke_width=1 if bold_row else stroke,
                          stroke_fill=0)
            x += widths[index] + CELL_PAD
        return top + line_h * max(len(c) for c in cells)

    if wrapped_head:
        y = draw_cells(wrapped_head, head_font, True) + 4
        draw.line([MARGIN, y, WIDTH_PX - MARGIN, y], fill=0, width=2)
        y += 6

    for position, cells in enumerate(wrapped_rows):
        y = draw_cells(cells, font, False)
        if grid and position < len(wrapped_rows) - 1:
            y += 3
            draw.line([MARGIN, y, WIDTH_PX - MARGIN, y], fill=0, width=1)
            y += 3
        else:
            y += 2

    bbox = img.point(lambda p: 0 if p > 128 else 255, mode="1").getbbox()
    if bbox:
        img = img.crop((0, 0, WIDTH_PX, min(height, bbox[3] + MARGIN)))

    return pil_to_rows(img.point(lambda p: 255 if p > 128 else 0, mode="1"))
