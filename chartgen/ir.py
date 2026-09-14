"""Representacion intermedia.

Todo frontend (GP5, MIDI, transcripcion de audio) produce un `Song`; todo
backend (.chart, .mid) lo consume. Ningun frontend debe conocer el formato de
salida ni viceversa.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .tempo import TempoMap

# Carriles. 0-4 son los botones; 7 es nota abierta (open).
GREEN, RED, YELLOW, BLUE, ORANGE = 0, 1, 2, 3, 4
OPEN = 7

# Instrumentos -> sufijo de la seccion en el .chart.
INSTRUMENTS = {
    "guitar": "Single",
    "bass": "DoubleBass",
    "rhythm": "DoubleRhythm",
    "coop": "DoubleGuitar",
    "keys": "Keyboard",
    "drums": "Drums",
}

DIFFICULTIES = ("Easy", "Medium", "Hard", "Expert")


@dataclass
class Note:
    tick: int
    fret: int
    sustain: int = 0
    force: bool = False
    """Invierte el HOPO natural que el juego deduce por distancia (65 ticks)."""
    tap: bool = False
    cymbal: bool = False
    """Solo bateria: marca el carril como platillo en vez de tom (Pro Drums)."""

    def __post_init__(self) -> None:
        if self.fret not in (0, 1, 2, 3, 4, OPEN):
            raise ValueError(f"Carril invalido: {self.fret}")
        if self.sustain < 0:
            raise ValueError(f"Sustain negativo en el tick {self.tick}")


@dataclass
class Phrase:
    """Frase de Star Power (`S 2 <duracion>` en el .chart)."""

    tick: int
    length: int


@dataclass
class Track:
    instrument: str = "guitar"
    difficulty: str = "Expert"
    notes: list[Note] = field(default_factory=list)
    star_power: list[Phrase] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.instrument not in INSTRUMENTS:
            raise ValueError(f"Instrumento desconocido: {self.instrument}")
        if self.difficulty not in DIFFICULTIES:
            raise ValueError(f"Dificultad desconocida: {self.difficulty}")

    @property
    def section_name(self) -> str:
        return f"{self.difficulty}{INSTRUMENTS[self.instrument]}"

    def sorted_notes(self) -> list[Note]:
        return sorted(self.notes, key=lambda n: (n.tick, n.fret))


@dataclass
class Event:
    """Evento global: seccion, letra o marca de frase."""

    tick: int
    text: str

    @classmethod
    def section(cls, tick: int, name: str) -> Event:
        return cls(tick, f"section {name}")

    @classmethod
    def lyric(cls, tick: int, syllable: str) -> Event:
        return cls(tick, f"lyric {syllable}")


@dataclass
class Metadata:
    name: str = "Unknown Song"
    artist: str = "Unknown Artist"
    album: str = ""
    genre: str = ""
    year: str = ""
    charter: str = "chartgen"
    preview_start: float = 0.0
    """Segundos desde el inicio del archivo de audio (ya con padding)."""
    song_length_ms: int = 0
    diff_guitar: int = -1
    diff_bass: int = -1
    diff_drums: int = -1
    diff_vocals: int = -1
    album_track: int = 0
    """Numero de pista. Clone Hero lo usa para ordenar dentro de un album."""
    delay_ms: int = 0
    """Solo si el audio NO se pre-padeo. El pipeline padea, asi que queda en 0."""
    loading_phrase: str = ""


@dataclass
class Song:
    metadata: Metadata = field(default_factory=Metadata)
    tempo_map: TempoMap = field(default_factory=TempoMap)
    tracks: list[Track] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    origin_tick: int = 0
    """Tick donde empieza el compas 1 de la musica, despues del lead-in.

    Lo necesita el alineado: para encajar un chart sobre otro mapa de tempo hay
    que saber respecto a que punto medir las posiciones musicales.
    """

    def track(self, instrument: str, difficulty: str = "Expert") -> Track | None:
        for t in self.tracks:
            if t.instrument == instrument and t.difficulty == difficulty:
                return t
        return None

    def add_track(self, track: Track) -> Track:
        if self.track(track.instrument, track.difficulty) is not None:
            raise ValueError(f"La pista {track.section_name} ya existe")
        self.tracks.append(track)
        return track

    def sections(self) -> list[Event]:
        return [e for e in self.events if e.text.startswith("section ")]
