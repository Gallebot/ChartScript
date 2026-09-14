"""`tempo_from_midi`: el mapa que sale del propio MIDI cuando no hay audio.

No se comprueba "que salga un mapa": se comprueba la CONVENCION que el resto del
pipeline da por hecha, porque romperla no hace fallar nada visiblemente y en
cambio desplaza el chart entero respecto al audio.

La convencion, tal como la fija `tempo_fit.fit` y la reproduce este modulo:

- el tick 0 es el principio del audio YA con lead-in;
- `origin_tick` es donde empieza el compas 1 de la musica y vale exactamente
  `PAD_SECONDS` segundos;
- `origin_tick` cae en un limite de compas entero;
- los ticks del MIDI se reescalan de su division a `RESOLUTION` (192).
"""

from __future__ import annotations

import pytest

import pipeline
from chartgen import PAD_SECONDS, RESOLUTION, smf
from chartgen.tempo import TempoMap

UN_MS = 0.001


# --------------------------------------------------------------------------
# La convencion del lead-in
# --------------------------------------------------------------------------

@pytest.mark.parametrize("division", [96, 192, 480, 960])
def test_the_origin_is_worth_exactly_the_lead_in(make_midi, division):
    """Con menos de 1 ms de error: es lo que separa el chart del audio."""
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(division=division, tempos=((0, 137.0),)))

    assert abs(tempo_map.seconds_at_tick(origin_tick) - PAD_SECONDS) < UN_MS


@pytest.mark.parametrize("bpm", [60.0, 76.923, 113.982, 137.0, 180.0])
def test_the_origin_falls_on_an_exact_measure_boundary(make_midi, bpm):
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(tempos=((0, bpm),)))

    ticks_per_measure = tempo_map.ticks_per_measure_at(0)
    assert origin_tick > 0
    assert origin_tick % ticks_per_measure == 0


def test_between_tick_zero_and_the_origin_there_is_a_single_tempo_event(make_midi):
    """El lead-in es un solo tramo: su BPM se elige para que cuadre con el pad."""
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(tempos=((0, 137.0),)))

    antes = [t for t in tempo_map.tempos if t.tick < origin_tick]
    assert [t.tick for t in antes] == [0]


def test_the_song_tempo_starts_at_the_origin_not_at_tick_zero(make_midi):
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(tempos=((0, 137.0),)))

    assert tempo_map.bpm_at_tick(origin_tick) == pytest.approx(137.0)


def test_the_measure_of_the_lead_in_matches_the_one_of_the_song(make_midi):
    tempo_map, origin_tick, note = pipeline.tempo_from_midi(
        make_midi(signatures=((0, 3, 4),)))

    assert tempo_map.time_signatures[0].numerator == 3
    assert tempo_map.ticks_per_measure_at(0) == RESOLUTION * 3
    assert origin_tick % (RESOLUTION * 3) == 0
    assert "compas 3/4" in note


# --------------------------------------------------------------------------
# Reescalado de la division
# --------------------------------------------------------------------------

def test_the_ticks_are_rescaled_from_their_division_to_192(make_midi):
    """Una negra son 480 ticks en el archivo y 192 en el chart.

    El cambio de tempo esta en el tick 1920 del MIDI (4 negras), asi que tiene
    que acabar 4 * 192 = 768 ticks despues del origen.
    """
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(division=480, tempos=((0, 140.0), (1920, 90.0))))

    cambios = {t.tick: t.bpm for t in tempo_map.tempos}
    assert cambios[origin_tick] == pytest.approx(140.0)
    assert cambios[origin_tick + 4 * RESOLUTION] == pytest.approx(90.0)


def test_a_division_finer_than_ours_also_lands_on_the_right_tick(make_midi):
    """Division 960: dos ticks del archivo son uno del chart... casi.

    960 -> 192 es dividir por 5, asi que una negra (960) son 192 y media negra
    (480) son 96. Nada que redondear.
    """
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(division=960, tempos=((0, 120.0), (480, 90.0))))

    cambios = {t.tick: t.bpm for t in tempo_map.tempos}
    assert cambios[origin_tick + RESOLUTION // 2] == pytest.approx(90.0)


def test_a_division_coarser_than_ours_is_scaled_up(make_midi):
    tempo_map, origin_tick, note = pipeline.tempo_from_midi(
        make_midi(division=96, tempos=((0, 120.0), (96, 100.0))))

    assert "division 96" in note
    cambios = {t.tick: t.bpm for t in tempo_map.tempos}
    assert cambios[origin_tick + RESOLUTION] == pytest.approx(100.0)


def test_a_time_signature_change_is_rescaled_too(make_midi):
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(division=480, signatures=((0, 4, 4), (3840, 3, 4))))

    ticks = {s.tick: (s.numerator, s.denominator)
             for s in tempo_map.time_signatures}
    assert ticks[origin_tick + 8 * RESOLUTION] == (3, 4)


# --------------------------------------------------------------------------
# Varios eventos de tempo
# --------------------------------------------------------------------------

def test_several_tempo_changes_all_survive(make_midi):
    tempos = ((0, 100.0), (960, 110.0), (1920, 120.0), (2880, 130.0))
    tempo_map, origin_tick, note = pipeline.tempo_from_midi(
        make_midi(division=480, tempos=tempos))

    # 4 del archivo + el evento del lead-in en el tick 0.
    assert len(tempo_map.tempos) == 5
    assert "4 eventos de tempo" in note
    assert [t.bpm for t in tempo_map.tempos[1:]] == [100.0, 110.0, 120.0, 130.0]


def test_the_map_is_monotonic_in_seconds(make_midi):
    """Cualquier mapa mal construido se delata aqui antes que en el juego."""
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(division=480, tempos=((0, 90.0), (1920, 150.0), (3840, 70.0))))

    ticks = [0, origin_tick] + [t.tick for t in tempo_map.tempos] + [100000]
    seconds = [tempo_map.seconds_at_tick(t) for t in sorted(set(ticks))]
    assert seconds == sorted(seconds)


def test_the_round_trip_tick_to_seconds_to_tick_holds_at_the_origin(make_midi):
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(division=480, tempos=((0, 113.982), (1920, 90.5))))

    assert tempo_map.tick_at_seconds(PAD_SECONDS) == origin_tick


# --------------------------------------------------------------------------
# El primer evento de tempo NO esta en el tick 0
# --------------------------------------------------------------------------

def test_a_first_tempo_event_off_tick_zero_is_extended_backwards(make_midi):
    """Es lo que hace cualquier secuenciador: el tramo inicial hereda ese BPM.

    Sin esto el mapa no arrancaria, porque `TempoMap` exige un evento en el 0.
    """
    midi = make_midi(division=480, tempos=((960, 100.0),))
    tempo_map, origin_tick, note = pipeline.tempo_from_midi(midi)

    assert abs(tempo_map.seconds_at_tick(origin_tick) - PAD_SECONDS) < UN_MS
    # El BPM de la cancion es el del primer evento, aunque estuviera en el 960.
    assert "100 BPM inicial" in note
    assert tempo_map.bpm_at_tick(origin_tick) == pytest.approx(100.0)
    # Y desde el origen no hay ningun cambio: el 960 traia el mismo BPM.
    assert tempo_map.bpm_at_tick(origin_tick + 2 * RESOLUTION) == pytest.approx(100.0)


def test_a_late_first_tempo_still_lets_a_later_change_through(make_midi):
    midi = make_midi(division=480, tempos=((960, 100.0), (2880, 150.0)))
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(midi)

    cambios = {t.tick: t.bpm for t in tempo_map.tempos}
    assert cambios[origin_tick + 6 * RESOLUTION] == pytest.approx(150.0)
    assert abs(tempo_map.seconds_at_tick(origin_tick) - PAD_SECONDS) < UN_MS


def test_a_midi_with_no_tempo_at_all_falls_back_to_120(make_midi, tmp_path):
    """Sin evento de tempo, 120 BPM es lo que asume cualquier lector de SMF."""
    part = [smf.text(0, smf.META_TRACK_NAME, "bass"),
            smf.note_on(0, 60, 100, 0), smf.note_off(480, 60, 0)]
    path = tmp_path / "sin_tempo.mid"
    path.write_bytes(smf.render([part], 480))

    tempo_map, origin_tick, note = pipeline.tempo_from_midi(path)

    assert "120 BPM inicial" in note
    assert tempo_map.bpm_at_tick(origin_tick) == pytest.approx(120.0)
    assert abs(tempo_map.seconds_at_tick(origin_tick) - PAD_SECONDS) < UN_MS


# --------------------------------------------------------------------------
# Compases raros
# --------------------------------------------------------------------------

def test_an_absurd_meter_change_does_not_bring_the_generation_down(make_midi):
    """Un compas corrupto no debe costar la cancion entera.

    Ojo con lo que este test NO puede provocar: el `except ValueError` que
    `tempo_from_midi` pone para los denominadores que no son potencia de 2 es
    inalcanzable desde un archivo, porque `smf.parse` devuelve el denominador
    como `2 ** payload[1]` y eso siempre lo es. Lo que si llega de un archivo
    roto es un exponente absurdo, y lo que se comprueba aqui es que el lead-in
    y el origen siguen siendo utilizables.
    """
    midi = make_midi(division=480, signatures=((0, 4, 4), (1920, 3, 4)))
    data = bytearray(midi.read_bytes())
    # El payload de un 0x58 es (numerador, exponente, 24, 8): se pone un
    # exponente imposible en el SEGUNDO cambio de compas.
    posicion = data.rindex(bytes([0xFF, 0x58, 0x04]))
    data[posicion + 4] = 3          # numerador
    data[posicion + 5] = 200        # denominador = 2**200
    midi.write_bytes(bytes(data))

    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(midi)

    assert tempo_map.time_signatures[0].numerator == 4
    assert origin_tick % tempo_map.ticks_per_measure_at(0) == 0
    assert abs(tempo_map.seconds_at_tick(origin_tick) - PAD_SECONDS) < UN_MS


def test_a_compound_meter_still_puts_the_origin_on_a_measure_boundary(make_midi):
    """En 6/8 el lead-in se mide en 6/4 y el compas real entra en el origen.

    Es raro visto en el editor, pero la propiedad que importa se mantiene: el
    origen cae en un limite de compas del mapa y vale `PAD_SECONDS`.
    """
    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(
        make_midi(division=480, signatures=((0, 6, 8),)))

    assert origin_tick % tempo_map.ticks_per_measure_at(0) == 0
    assert abs(tempo_map.seconds_at_tick(origin_tick) - PAD_SECONDS) < UN_MS
    assert tempo_map.time_signature_at_tick(origin_tick).denominator == 8


# --------------------------------------------------------------------------
# Errores
# --------------------------------------------------------------------------

def test_something_that_is_not_a_midi_gives_a_pipeline_error(tmp_path):
    basura = tmp_path / "no_es_un_midi.mid"
    basura.write_bytes(b"esto no es un Standard MIDI File")

    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.tempo_from_midi(basura)
    assert basura.name in str(exc.value)


def test_an_smpte_midi_is_refused_with_a_readable_reason(tmp_path):
    """Division con el bit alto puesto: mide en fotogramas, no en negras."""
    cuerpo = smf.render([[smf.text(0, smf.META_TRACK_NAME, "x")]], 480)
    data = bytearray(cuerpo)
    data[12:14] = (0xE8, 0x08)      # -24 fps, 8 ticks por fotograma
    path = tmp_path / "smpte.mid"
    path.write_bytes(bytes(data))

    with pytest.raises(pipeline.PipelineError):
        pipeline.tempo_from_midi(path)


# --------------------------------------------------------------------------
# El MIDI real
# --------------------------------------------------------------------------

def test_the_real_midi_keeps_the_convention(real_midi):
    tempo_map, origin_tick, note = pipeline.tempo_from_midi(real_midi)

    assert abs(tempo_map.seconds_at_tick(origin_tick) - PAD_SECONDS) < UN_MS
    assert origin_tick % tempo_map.ticks_per_measure_at(0) == 0
    assert "division 480" in note
    assert len(tempo_map.tempos) > 1
    assert tempo_map.resolution == RESOLUTION


def test_a_constant_map_is_interchangeable_with_the_one_from_the_midi():
    """Los tests rapidos usan `TempoMap.constant(120)`: misma resolucion y a
    120 BPM el lead-in cae en un tick redondo (4 negras = 768)."""
    tempo_map = TempoMap.constant(120)

    assert tempo_map.resolution == RESOLUTION
    assert tempo_map.tick_at_seconds(PAD_SECONDS) == 4 * RESOLUTION
