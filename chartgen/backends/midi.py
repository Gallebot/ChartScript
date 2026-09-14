"""Backend de `notes.mid`: IR -> el MIDI que lee Clone Hero.

## Por que existe si el `.chart` ya funciona

Porque hay cosas que el `.chart` no sabe decir: voces y Pro Drums solo existen en
MIDI. Y porque es el formato que entienden Rock Band, Moonscraper y el resto de
herramientas de la familia, asi que un `notes.mid` se puede abrir en sitios donde
un `.chart` no.

## El formato, medido y no recordado

Todo lo que hay aqui esta comprobado contra los `notes.mid` de la coleccion de
referencia con un parser escrito para eso. Lo que salio:

- **SMF formato 1, division 480** ticks por negra en los tres archivos mirados.
  No es la 192 del `.chart`: MIDI lee la division de su propia cabecera.
- Pista 0: solo tempo (meta 0x51) y compases (meta 0x58). Sin notas.
- Pista `EVENTS`: las secciones como texto `[section Chorus]`, igual que el
  `.chart`.
- Una pista por instrumento, nombrada `PART GUITAR`, `PART BASS`, etc.
- Dentro de cada una, **cada dificultad ocupa su octava**: Easy en 60-64, Medium
  en 72-76, Hard en 84-88 y Expert en 96-100. Un chart con las cuatro escribe las
  cuatro; el nuestro escribe solo Expert, que es lo que se vio en uno de los
  archivos reales (Aespa) y es valido.
- 116 en cualquier dificultad es **Star Power**. Aparecio en los tres archivos.
- 101 (Expert) es **force**, y hay un 103 suelto que es marcador de solo.
- En `PART DRUMS`, 110/111/112 marcan que el amarillo, azul o verde son TOM. La
  ausencia del marcador es lo que hace que suene a platillo, justo al reves que
  el `.chart`, donde el marcador (66/67/68) indica platillo.

## Lo que este backend no escribe

Voces, solos, fills de bateria y notas abiertas. Las abiertas necesitan un sysex
de Phase Shift y el IR hoy no las genera por ningun camino, asi que en vez de
inventarse la codificacion se avisa si aparecen.
"""

from __future__ import annotations

from pathlib import Path

from ..ir import OPEN, Note, Song, Track
from .. import smf

DIVISION = 480
"""Ticks por negra del archivo MIDI. Los tres `notes.mid` reales de la coleccion
usan 480, asi que se escribe 480 aunque el IR trabaje a 192."""

TRACK_NAMES = {
    "guitar": "PART GUITAR",
    "bass": "PART BASS",
    "rhythm": "PART RHYTHM",
    "coop": "PART GUITAR COOP",
    "keys": "PART KEYS",
    "drums": "PART DRUMS",
}

DIFFICULTY_BASE = {"Easy": 60, "Medium": 72, "Hard": 84, "Expert": 96}
"""Nota MIDI del carril verde en cada dificultad. Los cinco carriles van
seguidos hacia arriba."""

FORCE_OFFSET = 5
"""base + 5 invierte el HOPO automatico, igual que `N 5` en el `.chart`."""

TAP_NOTE = 104
STAR_POWER_NOTE = 116

DRUM_TOM_MARKERS = {2: 110, 3: 111, 4: 112}
"""Carril -> nota que lo marca como TOM.

Al reves que el `.chart`, que marca los PLATILLOS. Aqui la ausencia de marcador
significa platillo, asi que hay que escribir el marcador en las notas que NO
llevan `cymbal`."""

MIN_NOTE_TICKS = DIVISION // 32
"""Duracion de una nota sin sustain. Un note-on y su note-off en el mismo tick es
ambiguo para cualquier lector, asi que se le da el minimo que no se ve."""


class MidiBackendError(RuntimeError):
    pass


def scale(tick: int, resolution: int, division: int = DIVISION) -> int:
    """Pasa un tick del IR a la division del archivo MIDI.

    De 192 a 480 el factor es 2.5. Las notas cuantizadas caen en multiplos pares
    (192, 96, 64, 48, 32, 24), asi que salen exactas; las que quedaron fuera de
    rejilla pueden redondear medio tick, que a tempos normales es un milisegundo.
    """
    return int(round(tick * division / resolution))


def _lane_pitch(note: Note, base: int) -> int:
    if note.fret == OPEN:
        raise MidiBackendError(
            "Las notas abiertas necesitan un sysex de Phase Shift que este "
            "backend no escribe. Usa el .chart para ese chart."
        )
    return base + note.fret


def track_events(track: Track, resolution: int) -> list[smf.Event]:
    """Notas, flags y Star Power de una pista, ya en ticks de MIDI."""
    if track.difficulty not in DIFFICULTY_BASE:
        raise MidiBackendError(f"Dificultad sin hueco en MIDI: {track.difficulty}")
    base = DIFFICULTY_BASE[track.difficulty]
    drums = track.instrument == "drums"
    events: list[smf.Event] = []

    def pair(start: int, length: int, pitch: int) -> None:
        events.append(smf.note_on(start, pitch))
        events.append(smf.note_off(start + max(length, MIN_NOTE_TICKS), pitch))

    for note in track.sorted_notes():
        start = scale(note.tick, resolution)
        length = scale(note.sustain, resolution)
        pair(start, length, _lane_pitch(note, base))

        if note.force:
            pair(start, length, base + FORCE_OFFSET)
        if note.tap:
            pair(start, length, TAP_NOTE)
        # En MIDI se marca el TOM, no el platillo: es la convencion inversa a la
        # del .chart y confundirlas cambia el sonido de media bateria.
        if drums and not note.cymbal and note.fret in DRUM_TOM_MARKERS:
            pair(start, length, DRUM_TOM_MARKERS[note.fret])

    for phrase in track.star_power:
        pair(scale(phrase.tick, resolution), scale(phrase.length, resolution),
             STAR_POWER_NOTE)
    return events


def tempo_events(song: Song) -> list[smf.Event]:
    resolution = song.tempo_map.resolution
    events = [smf.tempo(scale(t.tick, resolution), t.bpm)
              for t in song.tempo_map.tempos]
    events += [smf.time_signature(scale(s.tick, resolution), s.numerator,
                                  s.denominator)
               for s in song.tempo_map.time_signatures]
    return events


def event_track(song: Song) -> list[smf.Event]:
    """Pista EVENTS: secciones y demas texto global."""
    resolution = song.tempo_map.resolution
    events = [smf.text(0, smf.META_TRACK_NAME, "EVENTS")]
    for event in song.events:
        if event.text.startswith("lyric "):
            continue  # las letras van en PART VOCALS, que este backend no escribe
        events.append(smf.text(scale(event.tick, resolution), smf.META_TEXT,
                               f"[{event.text}]"))
    return events


def render(song: Song, division: int = DIVISION) -> bytes:
    if not song.tracks:
        raise MidiBackendError("El Song no tiene ninguna pista que escribir.")

    tracks = [
        [smf.text(0, smf.META_TRACK_NAME, song.metadata.name or "chartgen")]
        + tempo_events(song),
        event_track(song),
    ]
    for track in song.tracks:
        if track.instrument not in TRACK_NAMES:
            raise MidiBackendError(
                f"Instrumento sin pista MIDI conocida: {track.instrument}")
        tracks.append(
            [smf.text(0, smf.META_TRACK_NAME, TRACK_NAMES[track.instrument])]
            + track_events(track, song.tempo_map.resolution)
        )
    return smf.render(tracks, division)


def write(song: Song, path: Path, division: int = DIVISION) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render(song, division))
    return path


# --------------------------------------------------------------------------
# MIDI de transcripcion: alturas reales, para abrirlo en un DAW
# --------------------------------------------------------------------------

def render_pitches(notes, tempo_map, division: int = DIVISION,
                   name: str = "chartgen") -> bytes:
    """Un MIDI corriente con las ALTURAS que se detectaron, no con carriles.

    No pasa por el IR y es a proposito: el IR ya redujo cada altura a un boton, y
    ahi se pierde justo lo que hace falta para editar la transcripcion en un DAW.
    Esto sale del transcriptor, antes de la reduccion.

    `notes` es cualquier cosa con `start`, `end` y `pitch` en segundos y MIDI.
    """
    def tick(second: float) -> int:
        return scale(tempo_map.tick_at_seconds(max(0.0, second)),
                     tempo_map.resolution, division)

    events = [smf.text(0, smf.META_TRACK_NAME, name)]
    for note in sorted(notes, key=lambda n: n.start):
        pitch = int(round(note.pitch))
        if not 0 <= pitch <= 127:
            continue
        start = tick(note.start)
        end = max(tick(note.end), start + 1)
        velocity = max(1, min(127, int(round(64 + 32 * (note.confidence - 0.5)))))
        events.append(smf.note_on(start, pitch, velocity))
        events.append(smf.note_off(end, pitch))

    tempos = [smf.tempo(scale(t.tick, tempo_map.resolution, division), t.bpm)
              for t in tempo_map.tempos]
    return smf.render([tempos, events], division)


def write_pitches(notes, tempo_map, path: Path, division: int = DIVISION,
                  name: str = "chartgen") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_pitches(notes, tempo_map, division, name))
    return path
