"""Carátula del album: 500x500 PNG."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 500


def _center_square(img: Image.Image) -> Image.Image:
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    return img.crop((left, top, left + side, top + side))


def from_image(src: Path, dst: Path, size: int = SIZE) -> Path:
    """Recorte centrado 1:1 y reescalado con antialiasing."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        img = img.convert("RGB")
        img = _center_square(img).resize((size, size), Image.LANCZOS)
        img.save(dst, "PNG", optimize=True)
    return dst


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int) -> ImageFont.ImageFont:
    for pt in range(start, 11, -2):
        try:
            font = ImageFont.truetype("arialbd.ttf", pt)
        except OSError:
            return ImageFont.load_default()
        if draw.textlength(text, font=font) <= max_width:
            return font
    return ImageFont.load_default()


def placeholder(dst: Path, title: str, artist: str, size: int = SIZE) -> Path:
    """Carátula generada cuando no hay arte real todavia (M0/M1)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (size, size), (24, 24, 28))
    draw = ImageDraw.Draw(img)
    margin = int(size * 0.08)

    draw.rectangle([margin, margin, size - margin, size - margin],
                   outline=(70, 70, 80), width=3)

    title_font = _fit_font(draw, title, size - 2 * margin - 20, int(size * 0.11))
    artist_font = _fit_font(draw, artist, size - 2 * margin - 20, int(size * 0.07))

    tw = draw.textlength(title, font=title_font)
    aw = draw.textlength(artist, font=artist_font)
    draw.text(((size - tw) / 2, size * 0.42), title, font=title_font, fill=(235, 235, 240))
    draw.text(((size - aw) / 2, size * 0.56), artist, font=artist_font, fill=(150, 150, 160))

    img.save(dst, "PNG", optimize=True)
    return dst
