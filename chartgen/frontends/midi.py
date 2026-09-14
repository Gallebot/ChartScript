"""Frontend de MIDI: un archivo ajeno -> notas del pipeline.

## Para que

Separar QUIEN transcribe de QUIEN hace el chart. Todo lo que este proyecto sabe
de charting —cuantizar contra el mapa de tempo, reducir a cinco carriles,
sustains, Star Power, empaquetar— no depende de que las notas las haya sacado
`pYIN`. Si otra herramienta transcribe mejor, se le enchufa su MIDI por aqui y el
resto del pipeline no se entera.

Y de paso permite MEDIRLA: un MIDI ajeno pasa por el mismo banco de pruebas que
lo nuestro, contra los mismos charts humanos, con las mismas metricas.

## Que se lee y que se ignora

Se leen las notas: inicio, fin y altura. Se ignoran velocidad, controladores y
todo lo demas, porque el IR no tiene donde ponerlos.

El tempo del archivo tambien se ignora **a proposito**. El mapa de tempo lo pone
M2 desde el audio, y es el que esta ajustado a la grabacion; el del MIDI ajeno
suele ser mas pobre. En el ejemplo que motivo esto, el archivo traia 5 eventos de
tempo y nuestra deteccion 55 sobre la misma cancion. Lo que si se aprovecha es su
division, para pasar sus ticks a segundos y de ahi a los nuestros.

## Como se decide que pista es que instrumento

Por el nombre de la pista y por el programa General MIDI, en ese orden. Los
transcriptores automaticos nombran sus pistas con etiquetas de su propio
vocabulario (`electric_bass`, `voice`, `synth_pad`), asi que se buscan trozos
conocidos dentro del nombre en vez de exigir una coincidencia exacta.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import smf
from ..transcribe import TranscribedNote


class MidiFrontendError(RuntimeError):
    pass


NAME_HINTS = (
    # (trozo en el nombre de la pista, instrumento del chart)
    ("bass", "bass"),
    ("drum", "drums"),
    ("percussion", "drums"),
    ("vocal", "vocals"),
    ("voice", "vocals"),
    ("sing", "vocals"),
    ("guitar", "guitar"),
    ("gtr", "guitar"),
    ("piano", "keys"),
    ("key", "keys"),
    ("synth", "keys"),
    ("pad", "keys"),
    ("organ", "keys"),
    ("lead", "guitar"),
)
"""Trozos de nombre que delatan el instrumento. Se prueban en orden.

`bass` va antes que `guitar` a proposito: "electric_bass" contiene las dos
palabras solo si se busca mal, pero "bass guitar" contiene las dos de verdad y
lo que es, es un bajo."""

PROGRAM_RANGES = (
    ((24, 31), "guitar"),
    ((32, 39), "bass"),
    ((0, 7), "keys"),
    ((16, 23), "keys"),
    ((80, 95), "keys"),
)
"""Familias de General MIDI que se pueden mapear a un instrumento del chart.

Las que no estan (cuerdas, metales, efectos) se dejan sin mapear en vez de
forzarlas: un chart de guitarra hecho con la seccion de cuerdas seria peor que
no tener chart."""


def classify(track: smf.MidiTrack) -> str | None:
    """Que instrumento del chart le corresponde a una pista, si alguno."""
    if track.is_percussion:
        return "drums"        # el canal 9 es percusion y no admite discusion
    name = (track.name or "").lower()
    for hint, instrument in NAME_HINTS:
        if hint in name:
            return instrument
    if track.program is not None:
        for (low, high), instrument in PROGRAM_RANGES:
            if low <= track.program <= high:
                return instrument
    return None


def ticks_to_seconds(division: int, tempos: list[tuple[int, float]]):
    """Conversor de los ticks del archivo a segundos, con SU mapa de tempo.

    Hace falta aunque luego se tire ese mapa: los tiempos hay que sacarlos de la
    linea de tiempo en la que fueron escritos. Solo despues se recolocan sobre el
    mapa ajustado al audio, que es lo que hace `frontends/transcription`.
    """
    puntos = sorted(tempos) or [(0, 120.0)]
    if puntos[0][0] != 0:
        puntos.insert(0, (0, 120.0))

    acumulado, previo = [0.0], puntos[0]
    for actual in puntos[1:]:
        acumulado.append(acumulado[-1] +
                         (actual[0] - previo[0]) * 60.0 / (previo[1] * division))
        previo = actual

    def convert(tick: int) -> float:
        indice = 0
        for i, (at, _) in enumerate(puntos):
            if at > tick:
                break
            indice = i
        at, bpm = puntos[indice]
        return acumulado[indice] + (tick - at) * 60.0 / (bpm * division)

    return convert


@dataclass
class SourceTrack:
    """Una pista del archivo, con su nombre y a que instrumento se la asigno."""

    name: str
    instrument: str | None
    notes: list[TranscribedNote]


def read_tracks(path: Path) -> list[SourceTrack]:
    """Todas las pistas con notas, clasificadas pero SIN agrupar.

    Se conserva el nombre original porque es la unica forma de referirse a una
    pista que el clasificador no supo colocar. En un archivo con `violin`, sin
    esto no habria manera de decir "esa, ponla en la guitarra".
    """
    path = Path(path)
    try:
        division, tracks = smf.parse(path.read_bytes())
    except smf.MidiReadError as exc:
        raise MidiFrontendError(f"{path.name}: {exc}") from None

    tempos = [t for track in tracks for t in track.tempos]
    convert = ticks_to_seconds(division, tempos)

    out: list[SourceTrack] = []
    for index, track in enumerate(tracks):
        if not track.notes:
            continue
        notes = []
        for start, end, pitch, velocity in track.notes:
            comienzo = convert(start)
            notes.append(TranscribedNote(
                start=comienzo,
                end=max(convert(end), comienzo),
                pitch=float(pitch),
                # Un MIDI no dice cuanta confianza tenia quien lo escribio. La
                # velocidad es lo mas parecido que hay, y cuando esta plana (que
                # es lo normal en un transcriptor) queda en 1.0 para todas.
                confidence=min(1.0, velocity / 100.0),
            ))
        notes.sort(key=lambda n: n.start)
        out.append(SourceTrack(name=track.name or f"pista {index}",
                               instrument=classify(track), notes=notes))
    return out


def read(path: Path) -> dict[str, list[TranscribedNote]]:
    """Notas agrupadas por instrumento del chart.

    Las pistas sin clasificar se descartan, y las que caigan en el mismo
    instrumento se juntan.
    """
    out: dict[str, list[TranscribedNote]] = {}
    for track in read_tracks(path):
        if track.instrument is None:
            continue
        out.setdefault(track.instrument, []).extend(track.notes)
    for notes in out.values():
        notes.sort(key=lambda n: n.start)
    return out


def apply_mapping(tracks: list[SourceTrack],
                  mapping: dict[str, str]) -> dict[str, list[TranscribedNote]]:
    """Agrupa las pistas siguiendo un mapeo explicito.

    La clave del mapeo puede ser el nombre de la pista (`violin`) o el
    instrumento que se le asigno (`vocals`), sin distinguir mayusculas. Lo
    primero es lo que permite rescatar una pista que el clasificador dejo fuera.

    **Varias fuentes al mismo destino se SUMAN**, no se pisan. Es justo lo que
    hace falta para montar una pista principal con lo que suena en cada momento:
    la voz en la estrofa, el teclado en el estribillo, el violin en el puente.
    """
    normal = {k.strip().lower(): v.strip() for k, v in mapping.items()}
    out: dict[str, list[TranscribedNote]] = {}
    for track in tracks:
        destino = normal.get(track.name.strip().lower())
        if destino is None and track.instrument:
            destino = normal.get(track.instrument.lower(), track.instrument)
        if destino is None:
            continue
        out.setdefault(destino, []).extend(track.notes)
    for notes in out.values():
        notes.sort(key=lambda n: n.start)
    return out


def describe(path: Path, mapping: dict[str, str] | None = None) -> list[str]:
    """Resumen legible de lo que se encontro. Para que el CLI lo imprima."""
    tracks = read_tracks(path)
    lines = [f"{len(tracks)} pistas con notas"]
    normal = {k.strip().lower(): v.strip() for k, v in (mapping or {}).items()}
    for track in tracks:
        destino = normal.get(track.name.lower())
        if destino is None and track.instrument:
            destino = normal.get(track.instrument, track.instrument)
        lines.append(f"  {track.name[:24]:<26} {len(track.notes):>5} notas  -> "
                     f"{destino or 'sin clasificar, se descarta'}")
    return lines
