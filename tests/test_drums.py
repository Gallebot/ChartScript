"""Bateria: percusion General MIDI -> carriles de Clone Hero.

Los ticks se pueden afirmar exactamente porque el mapa es constante a 120 BPM: la
negra son 192 ticks y el lead-in de 2 s son 4 negras (768 ticks), asi que una nota
en el segundo `k * 0.5` del audio original cae en el tick `768 + k * 192` sin
redondeos. Ver el docstring de `conftest.py`.
"""

from __future__ import annotations

import pytest

import pipeline
from chartgen import PAD_SECONDS, RESOLUTION
from chartgen import drums as drums_mod
from chartgen.frontends import transcription as tx_frontend
from chartgen.tempo import TempoMap
from conftest import note

NEGRA = RESOLUTION
ORIGEN_EN_TICKS = 4 * NEGRA          # PAD_SECONDS a 120 BPM
SEGUNDOS_POR_NEGRA = 0.5


@pytest.fixture
def mapa() -> TempoMap:
    return TempoMap.constant(120)


def golpes(pitches, separacion: float = SEGUNDOS_POR_NEGRA):
    """Un golpe por negra, en el orden dado."""
    return [note(i * separacion, pitch) for i, pitch in enumerate(pitches)]


def convertir(mapa, notes, subdivision: int = 4, tolerance: float = 0.045):
    return pipeline.drums_track(notes, mapa, 0, subdivision, tolerance)


# --------------------------------------------------------------------------
# La tabla de percusion
# --------------------------------------------------------------------------

def test_every_mapped_percussion_number_lands_on_its_declared_lane(mapa):
    """Se cruza contra `drums.MAPPING` entera, no contra una muestra.

    Los golpes se separan una negra para que cada uno tenga su propio tick y no
    se agrupen como acorde.
    """
    pitches = sorted(drums_mod.MAPPING)
    track, _ = convertir(mapa, golpes(pitches))

    por_tick = {n.tick: n for n in track.notes}
    assert len(por_tick) == len(pitches)
    for i, pitch in enumerate(pitches):
        esperado = drums_mod.MAPPING[pitch]
        salida = por_tick[ORIGEN_EN_TICKS + i * NEGRA]
        assert salida.fret == esperado.lane, f"nota {pitch}"
        assert salida.cymbal == esperado.cymbal, f"nota {pitch}"


@pytest.mark.parametrize("pitch,lane", [
    (35, drums_mod.KICK), (36, drums_mod.KICK),
    (38, drums_mod.RED), (40, drums_mod.RED),
    (42, drums_mod.YELLOW), (46, drums_mod.YELLOW),
    (48, drums_mod.YELLOW), (45, drums_mod.BLUE),
    (51, drums_mod.BLUE), (49, drums_mod.GREEN),
    (43, drums_mod.GREEN),
])
def test_the_pieces_of_a_standard_kit_land_where_the_reduction_says(mapa, pitch,
                                                                   lane):
    track, _ = convertir(mapa, golpes([pitch]))

    assert [n.fret for n in track.notes] == [lane]


def test_the_hi_hat_and_the_cymbals_come_out_marked_as_cymbals(mapa):
    track, _ = convertir(mapa, golpes([42, 51, 49]))

    assert [(n.fret, n.cymbal) for n in track.notes] == [
        (drums_mod.YELLOW, True), (drums_mod.BLUE, True), (drums_mod.GREEN, True)]


def test_the_toms_come_out_as_toms(mapa):
    track, _ = convertir(mapa, golpes([48, 45, 41]))

    assert [(n.fret, n.cymbal) for n in track.notes] == [
        (drums_mod.YELLOW, False), (drums_mod.BLUE, False),
        (drums_mod.GREEN, False)]


def test_an_unknown_piece_falls_into_the_filler_lane(mapa):
    """Cualquier numero sin mapear va al amarillo, que es el carril de relleno."""
    huerfano = next(p for p in range(1, 128) if p not in drums_mod.MAPPING)
    track, _ = convertir(mapa, golpes([huerfano]))

    assert [n.fret for n in track.notes] == [drums_mod.DEFAULT.lane]
    assert drums_mod.DEFAULT.lane == drums_mod.YELLOW


def test_the_kit_wins_over_the_latin_percussion_where_the_numbers_overlap(mapa):
    """66/67/68 estan en las dos tablas; `MAPPING` deja ganar al kit."""
    for pitch in (66, 67, 68):
        track, _ = convertir(mapa, golpes([pitch]))
        assert [n.fret for n in track.notes] == [drums_mod.MAPPING[pitch].lane]


# --------------------------------------------------------------------------
# Golpes simultaneos
# --------------------------------------------------------------------------

def test_two_hits_on_the_same_tick_come_out_as_two_notes_of_that_tick(mapa):
    """Un acorde de bateria, no dos eventos seguidos."""
    track, report = convertir(mapa, [note(0.0, 36), note(0.0, 38)])

    assert [(n.tick, n.fret) for n in track.notes] == [
        (ORIGEN_EN_TICKS, drums_mod.KICK), (ORIGEN_EN_TICKS, drums_mod.RED)]
    assert "1 eventos, 2 notas" in report


def test_two_hits_close_enough_to_snap_to_the_same_tick_merge(mapa):
    """20 ms de separacion caben dentro de la tolerancia del mismo tick."""
    track, _ = convertir(mapa, [note(0.0, 36), note(0.02, 38)])

    assert {n.tick for n in track.notes} == {ORIGEN_EN_TICKS}
    assert sorted(n.fret for n in track.notes) == [drums_mod.KICK, drums_mod.RED]


def test_the_same_lane_hit_twice_on_a_tick_is_one_note(mapa):
    """El carril es la clave: 38 y 40 son los dos la caja."""
    track, _ = convertir(mapa, [note(0.0, 38), note(0.0, 40)])

    assert [(n.tick, n.fret) for n in track.notes] == [(ORIGEN_EN_TICKS,
                                                        drums_mod.RED)]


def test_when_a_lane_sounds_as_tom_and_as_cymbal_the_cymbal_wins(mapa):
    """42 (charles, platillo) y 48 (tom agudo) comparten el amarillo."""
    track, _ = convertir(mapa, [note(0.0, 48), note(0.0, 42)])

    assert [(n.fret, n.cymbal) for n in track.notes] == [(drums_mod.YELLOW, True)]


# --------------------------------------------------------------------------
# El tope de golpes a la vez
# --------------------------------------------------------------------------

def test_never_more_lanes_at_once_than_the_measured_maximum(mapa):
    """MAX_SIMULTANEOUS sale de la coleccion de referencia: cuatro a la vez
    practicamente no existe."""
    cinco = [note(0.0, p) for p in (36, 38, 42, 45, 49)]   # kick, red, yel, blue, green
    track, _ = convertir(mapa, cinco)

    assert len(track.notes) == pipeline.MAX_SIMULTANEOUS
    assert len({n.fret for n in track.notes}) == pipeline.MAX_SIMULTANEOUS


def test_the_kick_survives_the_trim_because_it_is_the_pulse(mapa):
    cinco = [note(0.0, p) for p in (36, 38, 42, 45, 49)]
    track, _ = convertir(mapa, cinco)

    assert drums_mod.KICK in {n.fret for n in track.notes}


def test_after_the_kick_the_lowest_lanes_are_the_ones_kept(mapa):
    track, _ = convertir(mapa, [note(0.0, p) for p in (36, 38, 42, 45, 49)])

    assert sorted(n.fret for n in track.notes) == [drums_mod.KICK, drums_mod.RED,
                                                   drums_mod.YELLOW]


def test_without_a_kick_the_trim_just_keeps_the_lowest_lanes(mapa):
    track, _ = convertir(mapa, [note(0.0, p) for p in (38, 42, 45, 49)])

    assert sorted(n.fret for n in track.notes) == [drums_mod.RED,
                                                   drums_mod.YELLOW,
                                                   drums_mod.BLUE]


def test_exactly_the_maximum_is_not_trimmed(mapa):
    track, _ = convertir(mapa, [note(0.0, p) for p in (36, 38, 42)])

    assert len(track.notes) == 3


def test_the_cymbal_mark_survives_the_trim(mapa):
    """Recortar carriles no debe convertir un platillo en un tom."""
    track, _ = convertir(mapa, [note(0.0, p) for p in (36, 38, 42, 45, 49)])

    amarillo = next(n for n in track.notes if n.fret == drums_mod.YELLOW)
    assert amarillo.cymbal is True


def test_the_notes_come_out_ordered_by_tick(mapa):
    track, _ = convertir(mapa, golpes([36, 38, 42, 45, 49, 36, 38]))

    assert [n.tick for n in track.notes] == sorted(n.tick for n in track.notes)


# --------------------------------------------------------------------------
# Sin sustains ni flags
# --------------------------------------------------------------------------

def test_no_drum_note_carries_a_sustain(mapa):
    """En la coleccion de referencia los charts de bateria no llevan NINGUNO."""
    largas = [note(i * SEGUNDOS_POR_NEGRA, 36, duration=3.0) for i in range(8)]
    track, _ = convertir(mapa, largas)

    assert track.notes
    assert all(n.sustain == 0 for n in track.notes)


def test_no_drum_note_carries_force_or_tap(mapa):
    """No hay HOPO que calcular: en toda la coleccion hay un solo flag de force."""
    track, _ = convertir(mapa, golpes([36, 38, 42, 45]))

    assert all(not n.force and not n.tap for n in track.notes)


def test_the_track_is_the_drums_channel_at_expert(mapa):
    track, _ = convertir(mapa, golpes([36]))

    assert track.instrument == pipeline.DRUMS
    assert track.difficulty == "Expert"


def test_the_difficulty_can_be_asked_for(mapa):
    track, _ = pipeline.drums_track(golpes([36]), mapa, 0, 4, 0.045,
                                    difficulty="Hard")

    assert track.difficulty == "Hard"


# --------------------------------------------------------------------------
# _snap_hits
# --------------------------------------------------------------------------

def test_snap_hits_keys_by_tick_and_values_by_lane(mapa):
    hits, dropped = pipeline._snap_hits([note(0.0, 36), note(0.0, 42),
                                         note(0.5, 38)],
                                        mapa, 0, RESOLUTION // 4, 0.045)

    assert dropped == 0
    assert hits == {
        ORIGEN_EN_TICKS: {drums_mod.KICK: False, drums_mod.YELLOW: True},
        ORIGEN_EN_TICKS + NEGRA: {drums_mod.RED: False},
    }


def test_snap_hits_drops_what_falls_outside_the_tolerance(mapa):
    """Una rejilla de negras y un golpe a media negra: fuera."""
    hits, dropped = pipeline._snap_hits([note(0.0, 36), note(0.25, 38)],
                                        mapa, 0, RESOLUTION, 0.045)

    assert dropped == 1
    assert list(hits) == [ORIGEN_EN_TICKS]


def test_what_was_dropped_is_reported_and_not_hidden(mapa):
    notes = [note(0.0, 36), note(0.25, 38)]
    track, report = pipeline.drums_track(notes, mapa, 0, 1, 0.045)

    assert "1 de 2 golpes fuera de rejilla" in report
    assert len(track.notes) == 1


def test_a_report_without_losses_does_not_mention_them(mapa):
    _, report = convertir(mapa, golpes([36, 38]))

    assert "fuera de rejilla" not in report
    assert tx_frontend.label(4) in report


# --------------------------------------------------------------------------
# _drums_subdivision y el presupuesto de perdida
# --------------------------------------------------------------------------

def test_the_grid_is_refined_until_it_fits_the_drop_budget():
    """El caso que motivo `DRUMS_DROP_BUDGET`, reconstruido.

    90 golpes en la negra y 10 en la semicorchea siguiente. `choose_subdivision`
    se queda en negras porque explica el 90% (su umbral es el 80%), y ahi se
    perderia el 10% de la bateria: el doble del presupuesto. Tiene que afinar.

    A 60 BPM la semicorchea (0,25 s) queda lejos del tresillo de corchea
    (0,333 s), asi que la rejilla que de verdad explica los golpes es 1/16 y no
    una intermedia que los acepte por casualidad de la tolerancia.
    """
    mapa = TempoMap.constant(60)
    notes = ([note(i * 1.0, 36) for i in range(90)]
             + [note(i * 1.0 + 0.25, 38) for i in range(10)])
    times = [n.start + PAD_SECONDS for n in notes]

    gruesa, _ = tx_frontend.choose_subdivision(times, mapa, 0, tolerance=0.045)
    elegida = pipeline._drums_subdivision(notes, times, mapa, 0, 0.045)

    # La rejilla de partida pierde mas de lo permitido...
    _, perdidos = pipeline._snap_hits(notes, mapa, 0, RESOLUTION // gruesa, 0.045)
    assert perdidos > pipeline.DRUMS_DROP_BUDGET * len(notes)

    # ...y la elegida es mas fina y ya cumple.
    assert elegida > gruesa
    assert elegida in tx_frontend.SUBDIVISIONS
    _, perdidos_final = pipeline._snap_hits(notes, mapa, 0,
                                            RESOLUTION // elegida, 0.045)
    assert perdidos_final <= pipeline.DRUMS_DROP_BUDGET * len(notes)


def test_it_never_chooses_a_grid_coarser_than_choose_subdivision_would():
    """El criterio de origen ya evita el otro extremo; aqui solo se afina."""
    mapa = TempoMap.constant(120)
    notes = [note(i * 0.5, 36) for i in range(32)]
    times = [n.start + PAD_SECONDS for n in notes]

    gruesa, _ = tx_frontend.choose_subdivision(times, mapa, 0, tolerance=0.045)
    elegida = pipeline._drums_subdivision(notes, times, mapa, 0, 0.045)

    assert elegida >= gruesa


def test_material_all_on_the_beat_does_not_get_a_finer_grid_than_it_needs():
    mapa = TempoMap.constant(120)
    notes = [note(i * 0.5, 36) for i in range(32)]
    times = [n.start + PAD_SECONDS for n in notes]

    assert pipeline._drums_subdivision(notes, times, mapa, 0, 0.045) == 1


def test_scattered_attacks_do_not_blow_up_the_search():
    """Ataques al azar: se devuelve una rejilla valida en vez de petar."""
    import random

    rng = random.Random(7)
    mapa = TempoMap.constant(120)
    notes = [note(rng.uniform(0, 20), 36) for _ in range(120)]
    times = [n.start + PAD_SECONDS for n in notes]

    elegida = pipeline._drums_subdivision(notes, times, mapa, 0, 0.045)

    assert elegida in tx_frontend.SUBDIVISIONS
    track, _ = pipeline.drums_track(notes, mapa, 0, None, 0.045)
    assert track.notes


def test_a_fine_grid_does_not_accept_everything_regardless():
    """El tope del 40% del paso es lo que hace que el presupuesto discrimine.

    Sin el, `_snap_hits` usaba la tolerancia absoluta, y con 45 ms cualquier
    rejilla de paso menor de 90 ms aceptaba el 100% de los golpes esten donde
    esten. A 120 BPM eso ya pasaba en 1/24 (paso de 41,7 ms): ruido puro cumplia
    el presupuesto de perdida en una rejilla que no restringia nada, asi que
    `_drums_subdivision` la elegia creyendo que encajaba.

    Con el tope, ruido uniforme pierde alrededor del 20% en cualquier rejilla
    (la ventana cubre 0,8 del paso), que es justo lo que se quiere: que no haya
    ninguna rejilla en la que el ruido parezca gridado.
    """
    import random

    rng = random.Random(7)
    mapa = TempoMap.constant(120)
    notes = [note(rng.uniform(0, 20), 36) for _ in range(120)]
    times = [n.start + PAD_SECONDS for n in notes]

    elegida = pipeline._drums_subdivision(notes, times, mapa, 0, 0.045)
    paso_en_segundos = (RESOLUTION // elegida) * 60.0 / (120 * RESOLUTION)
    _, perdidos = pipeline._snap_hits(notes, mapa, 0, RESOLUTION // elegida, 0.045)

    # La rejilla elegida es fina (donde antes se colaba todo) y aun asi el ruido
    # no pasa el presupuesto del 5%: no hay donde esconderlo.
    assert paso_en_segundos < 2 * 0.045
    assert perdidos > pipeline.DRUMS_DROP_BUDGET * len(notes)

    # Y ninguna rejilla del abanico lo deja pasar entero.
    for subdivision in tx_frontend.SUBDIVISIONS:
        _, fuera = pipeline._snap_hits(notes, mapa, 0,
                                       RESOLUTION // subdivision, 0.045)
        assert fuera > 0, f"1/{subdivision * 4} acepto ruido uniforme completo"


def test_a_fixed_subdivision_is_respected_without_any_search(mapa):
    track, report = pipeline.drums_track(golpes([36, 38, 42]), mapa, 0,
                                         subdivision=8, tolerance=0.045)

    assert tx_frontend.label(8) in report
    assert all(n.tick % (RESOLUTION // 8) == 0 for n in track.notes)


def test_the_budget_is_five_percent():
    assert pipeline.DRUMS_DROP_BUDGET == 0.05
    assert pipeline.MAX_SIMULTANEOUS == 3


# --------------------------------------------------------------------------
# Errores
# --------------------------------------------------------------------------

def test_a_drums_channel_with_no_hits_is_an_error_not_an_empty_track(mapa):
    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.drums_track([], mapa, 0, 4, 0.045)

    assert "ningun golpe" in str(exc.value)


# --------------------------------------------------------------------------
# El MIDI real
# --------------------------------------------------------------------------

def test_the_real_drums_track_respects_every_invariant(real_midi):
    """Las mismas propiedades sobre 526 golpes reales y un mapa del propio MIDI."""
    from chartgen.frontends import midi as midi_frontend

    tempo_map, origin_tick, _ = pipeline.tempo_from_midi(real_midi)
    pista = next(t for t in midi_frontend.read_tracks(real_midi)
                 if t.name == "drums")
    track, report = pipeline.drums_track(pista.notes, tempo_map, origin_tick,
                                         None, 0.045)

    assert len(pista.notes) == 526
    assert track.notes
    assert all(n.sustain == 0 for n in track.notes)
    assert all(n.fret in (drums_mod.KICK, drums_mod.RED, drums_mod.YELLOW,
                          drums_mod.BLUE, drums_mod.GREEN) for n in track.notes)

    por_tick: dict[int, int] = {}
    for n in track.notes:
        por_tick[n.tick] = por_tick.get(n.tick, 0) + 1
    assert max(por_tick.values()) <= pipeline.MAX_SIMULTANEOUS
    assert all(t >= origin_tick for t in por_tick)
    assert "rejilla" in report
