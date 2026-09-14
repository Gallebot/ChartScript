"""Generacion de song.ini."""

from __future__ import annotations

import configparser
import io
from pathlib import Path

from .ir import Metadata


def render(md: Metadata) -> str:
    parser = configparser.ConfigParser()
    parser.optionxform = str  # Clone Hero es case-insensitive, pero no lo destrozamos.
    parser["Song"] = {
        "name": md.name,
        "artist": md.artist,
        "album": md.album,
        "genre": md.genre,
        "year": md.year,
        "charter": md.charter,
        "song_length": str(md.song_length_ms),
        "preview_start_time": str(int(round(md.preview_start * 1000))),
        "diff_guitar": str(md.diff_guitar),
        "diff_bass": str(md.diff_bass),
        "diff_drums": str(md.diff_drums),
        "diff_vocals": str(md.diff_vocals),
        "album_track": str(md.album_track),
        "delay": str(md.delay_ms),
        "loading_phrase": md.loading_phrase,
    }
    buf = io.StringIO()
    parser.write(buf)
    return buf.getvalue()


def write(md: Metadata, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sin BOM: el escaner de Clone Hero no lo tolera en la primera clave.
    path.write_text(render(md), encoding="utf-8")
    return path
