"""Ensamblado de la carpeta final que lee Clone Hero."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import PAD_SECONDS, art, audio, song_ini
from .backends import chart as chart_backend
from .backends import midi as midi_backend
from .ir import Song

_ILLEGAL = re.compile(r'[<>:"/\|?*\x00-\x1f]')


def _clean(part: str) -> str:
    part = _ILLEGAL.sub("", part or "")
    return re.sub(r"\s+", " ", part).strip(" .")


def safe_dirname(artist: str, title: str) -> str:
    parts = [p for p in (_clean(artist), _clean(title)) if p]
    return " - ".join(parts) or "Untitled"


class OverwriteError(RuntimeError):
    pass


@dataclass
class BuildResult:
    folder: Path
    files: list[Path]

    def describe(self) -> str:
        lines = [f"Carpeta: {self.folder}"]
        for f in sorted(self.files):
            lines.append(f"  {f.name:<12} {f.stat().st_size:>9,} bytes")
        return "\n".join(lines)


def build(
    song: Song,
    out_root: Path,
    audio_src: Path | None = None,
    art_src: Path | None = None,
    pad_seconds: float = PAD_SECONDS,
    normalize: bool = True,
    overwrite: bool = False,
    formats: tuple[str, ...] = ("chart",),
) -> BuildResult:
    """Escribe song.ogg, album.png, song.ini y el chart en una subcarpeta.

    `formats` elige entre `notes.chart` y `notes.mid`. Por defecto solo el
    primero: es el que esta verificado dentro del juego de punta a punta.

    **Ojo con pedir los dos**: cuando estan ambos, Clone Hero se queda con el
    `.mid` e ignora el `.chart`. No es un problema, pero conviene saber cual de
    los dos se esta jugando cuando algo no cuadra.

    Si `audio_src` es None genera silencio de la duracion del chart, para poder
    validar el escaneo del juego sin material con copyright.

    Se niega a pisar una carpeta que ya tenga un chart salvo con `overwrite`.
    Importa cuando `out_root` apunta a la biblioteca de canciones del usuario:
    una colision de "Artista - Titulo" destruiria un chart existente sin avisar.
    """
    unknown = set(formats) - {"chart", "mid"}
    if unknown or not formats:
        raise ValueError(f"Formatos de chart desconocidos: {sorted(unknown)}")

    md = song.metadata
    folder = Path(out_root) / safe_dirname(md.artist, md.name)
    filenames = {"chart": "notes.chart", "mid": "notes.mid"}
    if not overwrite and any((folder / filenames[f]).exists() for f in formats):
        raise OverwriteError(
            f"Ya existe un chart en {folder}.\n"
            "Usa --force para reemplazarlo, o cambia --title / --artist."
        )
    folder.mkdir(parents=True, exist_ok=True)

    ogg = folder / "song.ogg"
    if audio_src is not None:
        audio.prepare(Path(audio_src), ogg, pad_seconds=pad_seconds,
                      target_peak_db=-1.0 if normalize else None)
    else:
        last_tick = max(
            (n.tick + n.sustain for t in song.tracks for n in t.notes), default=0
        )
        audio.make_silence(ogg, song.tempo_map.seconds_at_tick(last_tick) + 2.0)

    md.song_length_ms = int(round(audio.probe_duration(ogg) * 1000))

    png = folder / "album.png"
    if art_src is not None:
        art.from_image(Path(art_src), png)
    else:
        art.placeholder(png, md.name, md.artist)

    ini = song_ini.write(md, folder / "song.ini")

    written = []
    if "chart" in formats:
        written.append(chart_backend.write(song, folder / filenames["chart"]))
    if "mid" in formats:
        written.append(midi_backend.write(song, folder / filenames["mid"]))

    return BuildResult(folder=folder, files=[ogg, png, ini, *written])
