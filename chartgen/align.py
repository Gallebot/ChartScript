"""Encaja las notas de una tablatura sobre el tempo real de una grabacion.

## El problema

Una tablatura de Guitar Pro dice "110 BPM" y lo mantiene clavado de principio a
fin. Una grabacion real respira: acelera en los estribillos, cede en los puentes,
y arrastra el pequeño error de que 110 no era exactamente su tempo. Un chart
sacado del tab y reproducido contra el audio original encaja al principio y se
despega a los treinta segundos.

## La idea

Las notas del tab NO estan en el tiempo: estan en posiciones musicales (compas y
pulso). Y el mapa de tempo que M2 ajusta al audio ya traduce pulso -> tiempo real.

Asi que no hay que mover las notas de su sitio musical. Hay que cambiarles el
mapa de tempo de debajo. Como ambas rejillas usan los mismos ticks por negra, la
posicion de cada nota es una traslacion constante; lo que corrige la deriva es
que el mapa de tempo nuevo sabe donde caen los pulsos de verdad.

## Lo que sigue sin resolverse solo

- Si la tablatura omite una repeticion que la grabacion si toca, o al reves, el
  encaje se rompe a partir de ese punto. Ninguna traslacion arregla una
  diferencia de estructura, y aqui se avisa en vez de disimularlo.
- Si el detector de beats se engancha a la mitad o al doble del tempo, todo se
  va. Se detecta comparando el BPM del tab con el detectado.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import PAD_SECONDS, RESOLUTION
from .beats import Beats
from .ir import Note, Phrase, Song, Track
from .tempo import TempoMap, TimeSignature
from .tempo_fit import TempoFit


class AlignmentError(RuntimeError):
    pass


@dataclass
class Alignment:
    offset_beats: int
    """En que pulso de la grabacion empieza el compas 1 de la tablatura."""
    tab_beats: int
    audio_beats: int
    tempo_ratio: float
    """BPM detectado / BPM de la tablatura. Deberia rondar 1."""
    confidence: float
    """0 a 1. Correlacion normalizada del desfase elegido frente al resto."""

    @property
    def beats_uncovered(self) -> int:
        """Pulsos del tab que se salen de los detectados en el audio."""
        return max(0, (self.offset_beats + self.tab_beats) - self.audio_beats)

    @property
    def tempo_looks_halved(self) -> bool:
        return self.tempo_ratio < 0.6 or self.tempo_ratio > 1.7

    def describe(self) -> str:
        return (
            f"desfase {self.offset_beats} pulsos | tab {self.tab_beats} pulsos, "
            f"audio {self.audio_beats} | ratio de tempo {self.tempo_ratio:.2f} | "
            f"confianza {self.confidence:.2f}"
        )

    def warnings(self) -> list[str]:
        out = []
        if self.tempo_looks_halved:
            out.append(
                f"El tempo detectado es {self.tempo_ratio:.2f}x el de la tablatura. "
                "El detector se engancho a la mitad o al doble del pulso; el "
                "encaje no va a servir."
            )
        if self.beats_uncovered > 4:
            out.append(
                f"A la tablatura le sobran {self.beats_uncovered} pulsos sobre el "
                "final de lo detectado. Suele significar que el tab repite algo "
                "que la grabacion no, o que el audio esta recortado."
            )
        if self.confidence < 0.15:
            out.append(
                "El desfase se eligio con poca confianza. Comprueba el resultado "
                "y ajustalo a mano con --offset-beats si hace falta."
            )
        return out


def note_density(song: Song, beats: int) -> list[float]:
    """Cuantas notas empiezan en cada pulso de la tablatura."""
    density = [0.0] * beats
    for track in song.tracks:
        for note in track.notes:
            index = (note.tick - song.origin_tick) // RESOLUTION
            if 0 <= index < beats:
                density[index] += 1.0
    return density


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    peak = max(values)
    return [v / peak for v in values] if peak > 0 else list(values)


def estimate_offset(song: Song, beats: Beats, tab_beats: int,
                    max_offset: int = 128) -> tuple[int, float]:
    """Estima en que pulso del audio arranca la tablatura.

    Correlaciona el patron de densidad de notas del tab con la energia de onset
    de cada pulso detectado. Una tablatura que empieza en el primer compas de la
    cancion da desfase 0; una que se salta una intro de ocho compases da 32.

    Sin energias de onset (`Beats.strengths` vacio) devuelve 0: es el caso mas
    frecuente y equivocarse ahi solo desplaza el chart un numero entero de
    pulsos, que se corrige a mano.
    """
    if not beats.strengths:
        return 0, 0.0

    tab = _normalize(note_density(song, tab_beats))
    audio = _normalize(list(beats.strengths))
    if not tab or not audio or sum(tab) == 0:
        return 0, 0.0

    limit = min(max_offset, max(0, len(audio) - 4))
    scores = []
    for offset in range(limit + 1):
        window = audio[offset:offset + len(tab)]
        if len(window) < 4:
            break
        scores.append(sum(a * b for a, b in zip(tab, window)) / len(window))

    if not scores:
        return 0, 0.0

    best = max(range(len(scores)), key=lambda i: scores[i])
    top = scores[best]
    average = sum(scores) / len(scores)
    confidence = 0.0 if top <= 0 else max(0.0, (top - average) / top)
    return best, confidence


def merge_maps(tab_map: TempoMap, audio_map: TempoMap, shift: int) -> TempoMap:
    """Tempos del audio, compases de la tablatura.

    Cada fuente aporta lo que sabe. El audio manda en el tempo: es la grabacion
    real y es lo que corrige la deriva. Pero el metro lo sabe la tablatura, no
    un detector de beats: en un tema que empieza en 5/4, quedarse con el 4/4 que
    infiere la deteccion desplazaria toda la rejilla de compases de Moonscraper.
    """
    signatures = [TimeSignature(max(0, s.tick + shift), s.numerator, s.denominator)
                  for s in tab_map.time_signatures]
    if not signatures or signatures[0].tick != 0:
        first = signatures[0] if signatures else TimeSignature(0, 4, 4)
        signatures.insert(0, TimeSignature(0, first.numerator, first.denominator))
    return TempoMap(audio_map.tempos, signatures, audio_map.resolution)


def align(tab_song: Song, fit: TempoFit, beats: Beats,
          offset_beats: int | None = None) -> tuple[Song, Alignment]:
    """Devuelve el chart de la tablatura sobre el mapa de tempo del audio."""
    notes = [n for t in tab_song.tracks for n in t.notes]
    if not notes:
        raise AlignmentError("La tablatura no tiene notas que encajar.")

    last = max(n.tick + n.sustain for n in notes)
    tab_beats = max(1, (last - tab_song.origin_tick) // RESOLUTION + 1)

    confidence = 1.0
    if offset_beats is None:
        offset_beats, confidence = estimate_offset(tab_song, beats, tab_beats)

    tab_bpm = tab_song.tempo_map.bpm_at_tick(tab_song.origin_tick)
    audio_bpm = fit.tempo_map.bpm_at_tick(fit.origin_tick)

    alignment = Alignment(
        offset_beats=offset_beats,
        tab_beats=tab_beats,
        audio_beats=len(fit.beat_ticks),
        tempo_ratio=audio_bpm / tab_bpm if tab_bpm else 0.0,
        confidence=confidence,
    )

    # La traslacion: del origen musical del tab al pulso elegido del audio.
    shift = fit.origin_tick + offset_beats * RESOLUTION - tab_song.origin_tick
    tempo_map = merge_maps(tab_song.tempo_map, fit.tempo_map, shift)

    aligned = Song(
        metadata=tab_song.metadata,
        tempo_map=tempo_map,
        origin_tick=fit.origin_tick,
        events=[replace(e, tick=e.tick + shift) for e in tab_song.events],
    )
    for track in tab_song.tracks:
        moved = Track(instrument=track.instrument, difficulty=track.difficulty)
        moved.notes = [
            Note(tick=n.tick + shift, fret=n.fret, sustain=n.sustain,
                 force=n.force, tap=n.tap)
            for n in track.notes
        ]
        moved.star_power = [
            Phrase(tick=p.tick + shift, length=p.length) for p in track.star_power
        ]
        aligned.add_track(moved)

    return aligned, alignment


INSTRUMENT_BANDS = {
    "bass": (0.0, 250.0),
    # La bateria ocupa todo el espectro: bombo abajo, caja y platillos arriba.
    # Es tambien lo que mejor destaca en casi cualquier mezcla, asi que sirve
    # para verificar un encaje cuando el instrumento charteado no se oye bien.
    "drums": (0.0, 8000.0),
    "guitar": (150.0, 2500.0),
    "rhythm": (150.0, 2500.0),
    "coop": (150.0, 2500.0),
    "keys": (150.0, 4000.0),
}
"""Banda de frecuencias donde buscar los ataques de cada instrumento."""


def onset_envelope(audio, band: tuple[float, float], sr: int = 22050,
                   hop_length: int = 256):
    """Envolvente de ataques limitada a una banda de frecuencias.

    Mirar solo la banda del instrumento es lo que hace utilizable esta señal:
    en el espectro completo mandan la caja y la voz, y el bajo queda enterrado.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio), mono=True, sr=sr)
    spectrum = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low, high = band
    mask = (freqs >= low) & (freqs <= high)
    if not mask.any():
        mask = slice(None)
    banded = spectrum[mask].sum(axis=0)
    env = librosa.onset.onset_strength(
        S=librosa.amplitude_to_db(banded[None, :], ref=np.max),
        sr=sr, hop_length=hop_length,
    )
    times = librosa.frames_to_time(np.arange(len(env)), sr=sr,
                                   hop_length=hop_length)
    return env, times


SEARCH_MARGIN = 4
"""Pulsos de holgura al acotar la busqueda del desfase.

La restriccion util no es la confianza sino que **la tablatura tiene que caber
dentro del audio**. Si el tab dura 393 pulsos y la grabacion 394, un desfase de
32 lo sacaria 31 pulsos por el final: es imposible, no improbable.

Se intento primero descartar por confianza y no funciona. Sobre una curva con
respuesta conocida y otra sin ella, ninguna formula las separaba:

| medida | hay respuesta | no la hay |
|---|---|---|
| (max-media)/max | 0.106 | 0.129 |
| (max-media)/desv | 1.806 | 1.561 |
| max/mediana | 1.125 | 1.124 |

La primera incluso puntuaba mejor el caso malo. Acotar el rango resuelve los tres
casos probados sin necesidad de distinguir señal de ruido."""


def usable_offsets(tab_beats: int, audio_beats: int, search: int = 32,
                   margin: int = SEARCH_MARGIN) -> int:
    """Hasta que desfase tiene sentido buscar, dado lo que dura cada cosa."""
    return max(0, min(search, audio_beats - tab_beats + margin))


def estimate_offset_from_audio(song: Song, fit: TempoFit, audio,
                               instrument: str = "bass", search: int = 32,
                               pad_seconds: float = PAD_SECONDS) -> tuple[int, float]:
    """Estima el desfase puntuando donde caerian las notas en el audio.

    Mejor que correlacionar densidad de notas contra la energia global: se usan
    los tiempos reales de cada nota, con resolucion menor que un pulso, y solo
    la banda del instrumento. Verificado en 24K Magic, donde el estimador
    anterior daba 5 pulsos y el correcto eran 3.
    """
    import numpy as np

    band = INSTRUMENT_BANDS.get(instrument, INSTRUMENT_BANDS["guitar"])
    env, times = onset_envelope(audio, band)

    ticks = sorted({n.tick for t in song.tracks for n in t.notes})
    if not ticks or len(env) == 0:
        return 0, 0.0

    tab_beats = (max(ticks) - song.origin_tick) // RESOLUTION + 1
    search = usable_offsets(tab_beats, len(fit.beat_ticks), search)

    scores = []
    for offset in range(search + 1):
        shift = fit.origin_tick + offset * RESOLUTION - song.origin_tick
        # El mapa de tempo vive en el audio YA padeado y la envolvente sale del
        # archivo original: hay que descontar el lead-in o se compara con dos
        # segundos de desfase.
        seconds = np.array([fit.tempo_map.seconds_at_tick(t + shift) - pad_seconds
                            for t in ticks])
        index = np.searchsorted(times, seconds)
        index = index[(index > 0) & (index < len(env))]
        scores.append(float(env[index].mean()) if len(index) else -1.0)

    if not scores:
        return 0, 0.0

    best = int(np.argmax(scores))
    top = scores[best]
    average = float(np.mean(scores))
    confidence = 0.0 if top <= 0 else max(0.0, min(1.0, (top - average) / top))
    return best, confidence
