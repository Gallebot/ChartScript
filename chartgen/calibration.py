"""Chart de calibracion: una nota por negra sobre un tempo constante.

Es la herramienta de verificacion que se reusa en cada milestone. En M0 prueba
que el juego escanea la carpeta y que el timing tick<->segundos es correcto; en
M2 se alimenta con los downbeats detectados y jugarlo revela al instante si el
beat tracking quedo corrido medio compas.
"""

from __future__ import annotations

from . import PAD_SECONDS, RESOLUTION
from .ir import GREEN, RED, Event, Metadata, Note, Phrase, Song, Track
from .tempo import TempoMap


def build(
    bpm: float = 120.0,
    seconds: float = 30.0,
    pad_seconds: float = PAD_SECONDS,
    metadata: Metadata | None = None,
) -> Song:
    tempo_map = TempoMap.constant(bpm)
    md = metadata or Metadata(
        name=f"Calibration {bpm:g}bpm",
        artist="chartgen",
        genre="Test",
        loading_phrase="Si las notas no caen sobre el click, revisa TempoMap.",
    )
    md.diff_guitar = 1
    song = Song(metadata=md, tempo_map=tempo_map)

    # Se ancla a una negra entera: a BPM no redondos el lead-in de 2 s no cae en
    # un tick exacto, y las notas quedarian fuera de la rejilla. Se redondea hacia
    # arriba para no comerse el lead-in.
    raw_start = tempo_map.tick_at_seconds(pad_seconds)
    start_tick = -(-raw_start // RESOLUTION) * RESOLUTION
    end_tick = tempo_map.tick_at_seconds(pad_seconds + seconds)
    ticks_per_measure = tempo_map.ticks_per_measure_at(start_tick)

    track = Track(instrument="guitar", difficulty="Expert")
    beat = start_tick
    while beat < end_tick:
        # Roja en el downbeat, verde en el resto: la rejilla de compases se ve.
        offset = (beat - start_tick) % ticks_per_measure
        track.notes.append(Note(tick=beat, fret=RED if offset == 0 else GREEN))
        beat += RESOLUTION

    # Una frase de Star Power de 4 compases para ejercitar tambien esa ruta.
    sp_start = start_tick + ticks_per_measure * 4
    if sp_start + ticks_per_measure * 4 < end_tick:
        track.star_power.append(Phrase(tick=sp_start, length=ticks_per_measure * 4))

    song.add_track(track)
    song.events.append(Event.section(start_tick, "Intro"))
    return song


def build_from_fit(fit, metadata: Metadata | None = None) -> Song:
    """Chart de verificacion sobre un tempo map ajustado a audio real.

    Una nota por beat detectado, roja en cada downbeat. Jugarlo contra la
    cancion es la prueba decisiva de M2: si el beat tracking quedo corrido medio
    compas, se oye al instante; ninguna metrica lo dice tan rapido.
    """
    md = metadata or Metadata(name="Beat check", artist="chartgen", genre="Test")
    md.diff_guitar = 1
    song = Song(metadata=md, tempo_map=fit.tempo_map,
                origin_tick=fit.origin_tick)

    track = Track(instrument="guitar", difficulty="Expert")
    for index, tick in enumerate(fit.beat_ticks):
        downbeat = index % fit.beats_per_measure == 0
        track.notes.append(Note(tick=tick, fret=RED if downbeat else GREEN))
    song.add_track(track)

    song.events.append(Event.section(fit.origin_tick, "Beat 1"))
    return song
