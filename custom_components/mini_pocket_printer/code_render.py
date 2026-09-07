"""QR codes et codes-barres rendus en 1 bit pour la tête thermique."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from .printer import WIDTH_PX
from .text_render import load_font, pil_to_rows

MARGIN = 8


def _caption(img: Image.Image, text: str, size: int, font_path: str | None,
             top: int) -> int:
    """Écrit une legende centree sous le code. Retourne le bas occupe."""
    font = load_font(size, font_path)
    draw = ImageDraw.Draw(img)
    width = draw.textbbox((0, 0), text, font=font)[2]
    draw.text(((WIDTH_PX - width) // 2, top), text, font=font, fill=0)
    return top + round(size * 1.3)


def build_qr_rows(data: str, scale: int = 8, border: int = 2,
                  label: str | None = None, label_size: int = 26,
                  font_path: str | None = None,
                  error_correction: str = "M") -> list[bytes]:
    """QR code centre sur la largeur du papier.

    Le module est agrandi par un facteur entier : un QR dont les modules
    tombent sur des demi-pixels devient illisible pour les lecteurs, alors
    qu'un agrandissement entier garde des bords francs.
    """
    import qrcode

    levels = {
        "L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H,
    }
    qr = qrcode.QRCode(
        error_correction=levels.get(error_correction.upper(), levels["M"]),
        border=border,
        box_size=1,
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    modules = len(matrix)

    # facteur entier le plus grand qui tienne dans la largeur utile
    scale = max(1, min(scale, (WIDTH_PX - 2 * MARGIN) // modules))
    side = modules * scale

    height = MARGIN + side + (round(label_size * 1.3) + 6 if label else 0) + MARGIN
    img = Image.new("L", (WIDTH_PX, height), 255)
    draw = ImageDraw.Draw(img)

    left = (WIDTH_PX - side) // 2
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                draw.rectangle(
                    [left + x * scale, MARGIN + y * scale,
                     left + (x + 1) * scale - 1, MARGIN + (y + 1) * scale - 1],
                    fill=0,
                )

    if label:
        _caption(img, label, label_size, font_path, MARGIN + side + 6)

    return pil_to_rows(img.point(lambda p: 255 if p > 128 else 0, mode="1"))


def build_barcode_rows(data: str, symbology: str = "code128",
                       height: int = 90, label: str | None = None,
                       label_size: int = 24, font_path: str | None = None,
                       show_text: bool = True) -> list[bytes]:
    """Code-barres 1D, mis à l'echelle sur la largeur du papier."""
    import barcode
    from barcode.writer import ImageWriter

    try:
        generator = barcode.get_barcode_class(symbology)
    except barcode.errors.BarcodeNotFoundError as err:
        raise ValueError(
            f"Symbologie inconnue : {symbology}. Disponibles : "
            + ", ".join(sorted(barcode.PROVIDED_BARCODES))
        ) from err

    try:
        code = generator(data, writer=ImageWriter())
    except barcode.errors.BarcodeError as err:
        raise ValueError(f"Donnee invalide pour {symbology} : {err}") from err

    # La valeur n'est jamais gravee par python-barcode : sa police et ses
    # distances sont exprimees en millimetres, et l'agrandissement sur 384 px
    # les fait chevaucher les barres. On la redessine nous-mêmes en dessous.
    buffer = io.BytesIO()
    code.write(buffer, options={
        "module_height": max(4.0, height / 6),
        "quiet_zone": 2,
        "write_text": False,
    })
    buffer.seek(0)
    bars = Image.open(buffer).convert("L")

    usable = WIDTH_PX - 2 * MARGIN
    ratio = usable / bars.width
    # NEAREST : un lissage transformerait les barres fines en gris, illisible
    # après seuillage.
    bars = bars.resize((usable, max(1, round(bars.height * ratio))), Image.NEAREST)

    value_size = round(label_size * 1.15)
    total = MARGIN + bars.height
    if show_text:
        total += round(value_size * 1.3) + 4
    if label:
        total += round(label_size * 1.3) + 4
    total += MARGIN

    img = Image.new("L", (WIDTH_PX, total), 255)
    img.paste(bars, (MARGIN, MARGIN))

    y = MARGIN + bars.height + 4
    if show_text:
        # code.get_fullcode() inclut la clé de controle calculee
        value = getattr(code, "get_fullcode", lambda: data)()
        y = _caption(img, value, value_size, font_path, y) + 4
    if label:
        _caption(img, label, label_size, font_path, y)

    return pil_to_rows(img.point(lambda p: 255 if p > 128 else 0, mode="1"))
