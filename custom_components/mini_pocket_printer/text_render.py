"""Rendu texte et image vers des lignes de 48 octets."""

from __future__ import annotations

import glob
import logging
import os

from PIL import Image, ImageDraw, ImageFont

_LOGGER = logging.getLogger(__name__)

from .printer import BYTES_PER_ROW, WIDTH_PX

BUNDLED_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# Polices embarquees avec l'intégration, sous licence OFL.
FONT_ALIASES = {
    "receipt": "CourierPrime-Regular.ttf",
    "receipt-bold": "CourierPrime-Bold.ttf",
    "dotmatrix": "VT323-Regular.ttf",
}

# Ordre de recherche par défaut : la police deposee par l'utilisateur prime,
# puis celles embarquees, puis celles du système.
FONT_CANDIDATES = [
    "/config/fonts/receipt.ttf",
    os.path.join(BUNDLED_DIR, "CourierPrime-Regular.ttf"),
    os.path.join(BUNDLED_DIR, "VT323-Regular.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]


def resolve_font(name: str | None) -> str | None:
    """Traduit un alias (receipt, dotmatrix...) en chemin de fichier."""
    if not name:
        return None
    if name.lower() in FONT_ALIASES:
        return os.path.join(BUNDLED_DIR, FONT_ALIASES[name.lower()])
    return name


FONT_DIRS = [
    "/config/fonts",
    "/usr/share/fonts",
    "/usr/local/share/fonts",
]


def _scan_fonts() -> list[str]:
    """Cherche des polices vectorielles sur le système, chasse fixe d'abord."""
    found: list[str] = []
    for directory in FONT_DIRS:
        for pattern in ("**/*.ttf", "**/*.otf"):
            found.extend(glob.glob(f"{directory}/{pattern}", recursive=True))
    mono = [f for f in found if "mono" in f.lower() or "courier" in f.lower()]
    return mono + [f for f in found if f not in mono]


def load_font(size: int, font_path: str | None = None):
    """Charge une police vectorielle, en dernier recours celle de Pillow.

    Le conteneur Home Assistant n'a pas les mêmes polices qu'un poste de
    travail. Sans police vectorielle, PIL retombe sur une fonte bitmap d'une
    dizaine de pixels qui ignoré la taille demandée : le texte sort minuscule
    quel que soit le corps choisi.
    """
    resolved = resolve_font(font_path)
    for candidate in ([resolved] if resolved else []) + FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, TypeError):
            continue

    for candidate in _scan_fonts():
        try:
            font = ImageFont.truetype(candidate, size)
            _LOGGER.debug("Police retenue : %s", candidate)
            return font
        except OSError:
            continue

    try:
        # Pillow 10.1+ : police vectorielle par défaut, mise à l'echelle.
        return ImageFont.load_default(size=size)
    except TypeError:
        _LOGGER.warning(
            "Aucune police vectorielle trouvee : le texte sera minuscule. "
            "Deposez un .ttf dans /config/fonts/receipt.ttf."
        )
        return ImageFont.load_default()


def columns_for(font, margin: int = 8) -> int:
    """Nombre de caractères tenant sur une ligne, mesure sur la police.

    Les tableaux du ticket sont alignes en comptant des caractères : il faut
    donc connaitre la largeur reelle du corps demandé, sinon les colonnes
    debordent des 384 px des que l'on grossit le texte.
    """
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    advance = draw.textlength("0" * 10, font=font) / 10
    if advance <= 0:
        return 24
    return max(8, int((WIDTH_PX - 2 * margin) // advance))


def pil_to_rows(bw: Image.Image) -> list[bytes]:
    """Image PIL mode '1', 384 px de large -> lignes de 48 octets, 1 = noir."""
    data = bw.tobytes()
    stride = (bw.width + 7) // 8
    rows = []
    for y in range(bw.height):
        line = bytes(0xFF ^ b for b in data[y * stride:(y + 1) * stride])
        rows.append(line.ljust(BYTES_PER_ROW, b"\x00")[:BYTES_PER_ROW])
    return rows


def _wrap(lines: list[str], font, max_width: int) -> list[str]:
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))

    def width(text: str) -> int:
        return draw.textbbox((0, 0), text, font=font)[2]

    out: list[str] = []
    for line in lines:
        if not line:
            out.append("")
            continue
        current = ""
        for word in line.split(" "):
            trial = f"{current} {word}".strip()
            if current and width(trial) > max_width:
                out.append(current)
                current = word
            else:
                current = trial
            while width(current) > max_width and len(current) > 1:
                cut = len(current)
                while cut > 1 and width(current[:cut]) > max_width:
                    cut -= 1
                out.append(current[:cut])
                current = current[cut:]
        out.append(current)
    return out


def text_to_rows(text: str, size: int = 28, font_path: str | None = None,
                 margin: int = 8, spacing: int = 6, align: str = "left",
                 wrap: bool = True, bold: bool = False) -> list[bytes]:
    """Seuil franc plutot que tramage : sur tête thermique le texte reste net."""
    text = text.replace("\\n", "\n").replace("\\t", "    ")
    font = load_font(size, font_path)
    stroke = 1 if bold else 0
    usable = WIDTH_PX - 2 * margin - 2 * stroke

    lines = text.split("\n")
    if wrap:
        lines = _wrap(lines, font, usable)

    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    line_h = max(draw.textbbox((0, 0), ln or "Ay", font=font)[3] for ln in lines)
    line_h += spacing + stroke

    img = Image.new("L", (WIDTH_PX, line_h * len(lines) + 2 * margin), 255)
    draw = ImageDraw.Draw(img)
    for index, line in enumerate(lines):
        text_w = draw.textbbox((0, 0), line, font=font, stroke_width=stroke)[2]
        if align == "center":
            x = (WIDTH_PX - text_w) // 2
        elif align == "right":
            x = WIDTH_PX - margin - text_w
        else:
            x = margin
        draw.text((x, margin + index * line_h), line, font=font, fill=0,
                  stroke_width=stroke, stroke_fill=0)

    return pil_to_rows(img.point(lambda p: 255 if p > 128 else 0, mode="1"))


MAGICS = {
    b"\x89PNG": "PNG", b"\xff\xd8\xff": "JPEG", b"GIF8": "GIF",
    b"RIFF": "WEBP", b"BM": "BMP", b"II*\x00": "TIFF", b"MM\x00*": "TIFF",
}


def _describe(data: bytes) -> str:
    """Identifié sommairement un contenu qui n'est pas une image."""
    head = data[:16]
    for magic, name in MAGICS.items():
        if head.startswith(magic):
            return f"{name} illisible"
    stripped = head.lstrip()
    if stripped[:5].lower() in (b"<!doc", b"<html"):
        return ("page HTML : l'URL renvoie une page web, pas un fichier image. "
                "Utilisez le lien direct vers le fichier")
    if stripped.startswith(b"<svg") or b"<svg" in data[:512].lower():
        return "SVG : format vectoriel non pris en charge, convertissez en PNG"
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        return "JSON : l'URL renvoie des donnees, probablement une erreur d'API"
    return f"format inconnu, premiers octets : {head[:8].hex()}"


def image_bytes_to_rows(data: bytes, threshold: int | None = None,
                        invert: bool = False) -> list[bytes]:
    """Image quelconque -> 384 px de large, tramage Floyd-Steinberg par défaut."""
    import io

    from PIL import ImageOps, UnidentifiedImageError

    if not data:
        raise ValueError("Source vide : aucun contenu recupere")

    try:
        img = Image.open(io.BytesIO(data)).convert("L")
    except UnidentifiedImageError as err:
        raise ValueError(
            f"Contenu non reconnu comme image ({len(data)} octets) : "
            f"{_describe(data)}"
        ) from err
    if invert:
        img = ImageOps.invert(img)
    if img.width != WIDTH_PX:
        height = max(1, round(img.height * WIDTH_PX / img.width))
        img = img.resize((WIDTH_PX, height), Image.LANCZOS)
    bw = (img.convert("1") if threshold is None
          else img.point(lambda p: 255 if p > threshold else 0, mode="1"))
    return pil_to_rows(bw)
