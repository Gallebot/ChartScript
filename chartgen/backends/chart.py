"""Backend .chart (formato texto de Moonscraper / Clone Hero)."""

from __future__ import annotations

from pathlib import Path

from ..ir import Note, Song

NEWLINE = "\r\n"
"""Moonscraper escribe CRLF. Clone Hero acepta ambos, pero mantenerlo evita
diffs ruidosos cuando el usuario reabre y guarda el chart en el editor.
"""


def _quote(value: str) -> str:
    """El .chart no define escapes dentro de cadenas: las comillas se sustituyen."""
    return '"' + str(value).replace('"', "'") + '"'


def _flag_lines(note: Note) -> list[str]:
    """Los flags son notas extra en el mismo tick: 5 = force, 6 = tap.

    En bateria, 66/67/68 marcan amarillo/azul/verde como platillo en vez de tom.
    """
    from ..drums import CYMBAL_MARKER

    out = []
    if note.force:
        out.append("N 5 0")
    if note.tap:
        out.append("N 6 0")
    if note.cymbal and note.fret in CYMBAL_MARKER:
        out.append(f"N {CYMBAL_MARKER[note.fret]} 0")
    return out


def render(song: Song) -> str:
    md = song.metadata
    tm = song.tempo_map
    lines: list[str] = []

    def section(name: str, body: list[str]) -> None:
        lines.append(f"[{name}]")
        lines.append("{")
        lines.extend(f"  {b}" for b in body)
        lines.append("}")

    section(
        "Song",
        [
            f"Name = {_quote(md.name)}",
            f"Artist = {_quote(md.artist)}",
            f"Charter = {_quote(md.charter)}",
            f"Album = {_quote(md.album)}",
            f"Year = {_quote(md.year)}",
            "Offset = 0",
            f"Resolution = {tm.resolution}",
            "Player2 = bass",
            "Difficulty = 0",
            f"PreviewStart = {md.preview_start:g}",
            "PreviewEnd = 0",
            f"Genre = {_quote(md.genre)}",
            'MediaType = "cd"',
            'MusicStream = "song.ogg"',
        ],
    )

    sync: list[tuple[int, int, str]] = []
    for ts in tm.time_signatures:
        # El denominador se omite cuando es 4 (el valor por defecto del formato).
        if ts.denominator == 4:
            sync.append((ts.tick, 0, f"TS {ts.numerator}"))
        else:
            sync.append((ts.tick, 0, f"TS {ts.numerator} {ts.denominator_exponent}"))
    for tempo in tm.tempos:
        sync.append((tempo.tick, 1, f"B {round(tempo.bpm * 1000)}"))
    sync.sort(key=lambda x: (x[0], x[1]))
    section("SyncTrack", [f"{tick} = {body}" for tick, _, body in sync])

    events = sorted(song.events, key=lambda e: e.tick)
    section("Events", [f"{e.tick} = E {_quote(e.text)}" for e in events])

    for track in song.tracks:
        body: list[str] = []
        rows: list[tuple[int, int, str]] = []
        for note in track.sorted_notes():
            rows.append((note.tick, 0, f"N {note.fret} {note.sustain}"))
            for flag in _flag_lines(note):
                rows.append((note.tick, 1, flag))
        for phrase in sorted(track.star_power, key=lambda p: p.tick):
            rows.append((phrase.tick, 2, f"S 2 {phrase.length}"))
        rows.sort(key=lambda x: (x[0], x[1]))
        body.extend(f"{tick} = {text}" for tick, _, text in rows)
        section(track.section_name, body)

    return NEWLINE.join(lines) + NEWLINE


def write(song: Song, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" para que render() controle los saltos de linea sin traduccion.
    path.write_text(render(song), encoding="utf-8", newline="")
    return path
