"""`build_song`: aplicar una asignacion canal -> pistas y salir con un `Song`.

Es la parte que se repite por variante, asi que lo que se comprueba es que la
asignacion se aplique tal cual la pidio el usuario (sumando lo que hay que sumar,
sin pisar nada) y que lo que no se puede hacer se diga en vez de salir raro.

Los `Prepared` se construyen a mano (fixture `make_prepared`): `prepare()` llama a
MusicBrainz y a la deteccion de beats, y ninguna de las dos cosas influye en lo
que hace `build_song`.
"""

from __future__ import annotations

import pytest

import pipeline
from chartgen import RESOLUTION
from chartgen.tempo import TempoMap
from conftest import note, source_track

NEGRA = RESOLUTION
ORIGEN_EN_TICKS = 4 * NEGRA          # PAD_SECONDS a 120 BPM


@pytest.fixture
def pistas():
    """Cuatro pistas con notas en instantes distintos, para poder distinguirlas."""
    return [
        source_track("voice", "vocals",
                     [note(i * 1.0, 60) for i in range(6)]),
        source_track("electric_piano", "keys",
                     [note(i * 1.0 + 0.5, 64) for i in range(6)]),
        source_track("synth_pad", "keys",
                     [note(i * 1.0 + 0.25, 67) for i in range(6)]),
        source_track("drums", "drums",
                     [note(i * 0.5, 36) for i in range(12)]),
    ]


# --------------------------------------------------------------------------
# Sumar varias pistas en un canal
# --------------------------------------------------------------------------

def test_several_tracks_in_one_channel_are_summed(make_prepared, pistas):
    """Es como se juntan dos teclados en `keys`. Nada se pisa."""
    prepared = make_prepared(pistas)

    song, _, _ = pipeline.build_song(
        prepared, {"keys": ["electric_piano", "synth_pad"]})

    ticks = sorted({n.tick for n in song.track("keys").notes})
    # Las dos pistas caen en instantes distintos (0,5 s y 0,25 s dentro de cada
    # segundo): 12 instantes en total, ninguno perdido por el camino.
    assert len(ticks) == 12
    assert ORIGEN_EN_TICKS + NEGRA in ticks           # electric_piano en 0,5 s
    assert ORIGEN_EN_TICKS + NEGRA // 2 in ticks      # synth_pad en 0,25 s


def test_summing_produces_a_single_track_for_the_channel(make_prepared, pistas):
    prepared = make_prepared(pistas)

    song, reports, _ = pipeline.build_song(
        prepared, {"keys": ["electric_piano", "synth_pad"]})

    assert [t.instrument for t in song.tracks] == ["keys"]
    assert list(reports) == ["keys"]


def test_the_summed_notes_are_sorted_before_quantizing(make_prepared):
    """Se ordenan por tiempo: dos pistas concatenadas llegan intercaladas."""
    pistas = [source_track("tarde", "guitar", [note(2.0, 60), note(3.0, 62)]),
              source_track("pronto", "guitar", [note(0.0, 64), note(1.0, 65)])]
    prepared = make_prepared(pistas)

    song, _, _ = pipeline.build_song(prepared, {"guitar": ["tarde", "pronto"]})

    ticks = [n.tick for n in song.track("guitar").notes]
    assert ticks == sorted(ticks)
    assert len(set(ticks)) == 4


def test_the_same_track_can_feed_two_channels(make_prepared, pistas):
    """Dos jugadores tocando la misma linea: lo que habilita `--allow-reuse`."""
    prepared = make_prepared(pistas)

    song, reports, _ = pipeline.build_song(
        prepared, {"guitar": ["voice"], "bass": ["voice"]})

    assert sorted(t.instrument for t in song.tracks) == ["bass", "guitar"]
    assert sorted(reports) == ["bass", "guitar"]
    assert {n.tick for n in song.track("guitar").notes}


def test_each_channel_gets_only_its_own_tracks(make_prepared, pistas):
    prepared = make_prepared(pistas)

    song, _, _ = pipeline.build_song(prepared, {"guitar": ["voice"],
                                                "keys": ["electric_piano"]})

    guitarra = {n.tick for n in song.track("guitar").notes}
    teclas = {n.tick for n in song.track("keys").notes}
    assert guitarra and teclas
    assert guitarra.isdisjoint(teclas)


# --------------------------------------------------------------------------
# Orden de los canales y de las pistas
# --------------------------------------------------------------------------

def test_the_tracks_come_out_in_the_canonical_channel_order(make_prepared, pistas):
    """No en el orden en que el usuario escribio la asignacion: en el de CHANNELS."""
    prepared = make_prepared(pistas)

    song, _, _ = pipeline.build_song(prepared, {"drums": ["drums"],
                                                "keys": ["electric_piano"],
                                                "guitar": ["voice"]})

    assert [t.instrument for t in song.tracks] == ["guitar", "keys", "drums"]


def test_the_drums_channel_goes_through_the_drums_path(make_prepared, pistas):
    """Se nota en el informe: la bateria cuenta eventos, lo melodico notas."""
    prepared = make_prepared(pistas)

    _, reports, _ = pipeline.build_song(prepared, {"drums": ["drums"],
                                                   "guitar": ["voice"]})

    assert "eventos" in reports["drums"]
    assert "encajadas" in reports["guitar"]


def test_the_song_carries_the_tempo_map_and_the_origin_of_the_prepared(make_prepared,
                                                                      pistas):
    mapa = TempoMap.constant(90)
    prepared = make_prepared(pistas, tempo_map=mapa, origin_tick=576)

    song, _, _ = pipeline.build_song(prepared, {"guitar": ["voice"]})

    assert song.tempo_map is mapa
    assert song.origin_tick == 576


# --------------------------------------------------------------------------
# Errores
# --------------------------------------------------------------------------

def test_an_unknown_channel_raises_and_lists_the_ones_there_are(make_prepared,
                                                                pistas):
    prepared = make_prepared(pistas)

    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.build_song(prepared, {"vocals": ["voice"]})

    mensaje = str(exc.value)
    assert "vocals" in mensaje
    for channel in pipeline.CHANNELS:
        assert channel in mensaje


@pytest.mark.parametrize("canal", ["Guitar", "GUITAR", "guitarra", "", "trombon"])
def test_build_song_does_not_guess_the_channel_name(make_prepared, pistas, canal):
    """No normaliza: eso es trabajo del CLI, que si lo hace."""
    prepared = make_prepared(pistas)

    with pytest.raises(pipeline.PipelineError):
        pipeline.build_song(prepared, {canal: ["voice"]})


def test_an_empty_assignment_raises(make_prepared, pistas):
    prepared = make_prepared(pistas)

    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.build_song(prepared, {})

    assert "vacia" in str(exc.value)


def test_an_assignment_of_channels_with_no_tracks_also_raises(make_prepared, pistas):
    """`{'guitar': []}` no es una asignacion: es una asignacion vacia."""
    prepared = make_prepared(pistas)

    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.build_song(prepared, {"guitar": [], "bass": []})

    assert "vacia" in str(exc.value)


def test_a_track_that_is_not_in_the_midi_raises_naming_it(make_prepared, pistas):
    prepared = make_prepared(pistas)

    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.build_song(prepared, {"guitar": ["trompeta"]})

    assert "trompeta" in str(exc.value)


def test_a_track_with_no_notes_is_named_in_the_error(make_prepared):
    """Antes se descartaba en silencio y el error acababa siendo "la asignacion
    esta vacia", que manda a buscar el problema al sitio equivocado."""
    pistas = [source_track("vacia", "guitar", []),
              source_track("drums", "drums", [note(i * 0.5, 36) for i in range(8)])]
    prepared = make_prepared(pistas)

    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.build_song(prepared, {"guitar": ["vacia"], "drums": ["drums"]})

    assert "vacia" in str(exc.value)
    assert "guitar" in str(exc.value)


def test_a_channel_with_no_tracks_is_simply_left_out(make_prepared):
    """Es como la combinatoria representa "este canal va vacio en esta variante",
    asi que es una lista vacia y no un error."""
    pistas = [source_track("drums", "drums", [note(i * 0.5, 36) for i in range(8)])]
    prepared = make_prepared(pistas)

    song, reports, _ = pipeline.build_song(prepared, {"guitar": [],
                                                     "drums": ["drums"]})

    assert [t.instrument for t in song.tracks] == ["drums"]
    assert "guitar" not in reports


# --------------------------------------------------------------------------
# Avisos
# --------------------------------------------------------------------------

def test_without_a_guitar_channel_the_warning_shows_up(make_prepared, pistas):
    """Clone Hero abre la cancion en guitarra: sin ella se ve vacia."""
    prepared = make_prepared(pistas)

    _, _, warnings = pipeline.build_song(prepared, {"bass": ["voice"],
                                                    "drums": ["drums"]})

    aviso = [w for w in warnings if "no hay canal de guitarra" in w]
    assert len(aviso) == 1
    # Y dice a que instrumento hay que cambiar en el juego.
    assert "bass" in aviso[0]


def test_with_a_guitar_channel_there_is_no_such_warning(make_prepared, pistas):
    prepared = make_prepared(pistas)

    _, _, warnings = pipeline.build_song(prepared, {"guitar": ["voice"],
                                                    "drums": ["drums"]})

    assert not any("no hay canal de guitarra" in w for w in warnings)


def test_a_drums_only_chart_is_warned_about_too(make_prepared, pistas):
    prepared = make_prepared(pistas)

    _, _, warnings = pipeline.build_song(prepared, {"drums": ["drums"]})

    assert any("no hay canal de guitarra" in w for w in warnings)


def test_the_warnings_of_the_melodic_frontend_are_prefixed_by_channel(make_prepared):
    """Un aviso de cuantizacion tiene que decir de que canal viene.

    Rejilla de negras fijada a mano y notas a 0,2 s del pulso: ni el desfase
    global (topado en 60 ms) las salva, asi que todas quedan fuera de rejilla y
    el frontend melodico tiene algo que decir.
    """
    fuera = [note(i * 0.5 + 0.2, 60) for i in range(40)]
    prepared = make_prepared([source_track("desplazada", "guitar", fuera)],
                             options=pipeline.Options(cleanup=False,
                                                      star_power=False,
                                                      subdivision=1))

    _, _, warnings = pipeline.build_song(prepared, {"guitar": ["desplazada"]})

    assert warnings
    assert all(w.startswith("guitar: ") for w in warnings)
    assert any("no encaja en ninguna rejilla" in w for w in warnings)


# --------------------------------------------------------------------------
# Star Power
# --------------------------------------------------------------------------

def test_star_power_is_added_to_the_drums_when_it_is_asked_for(make_prepared):
    """La bateria no pasa por `charting`, asi que sus frases se ponen aparte."""
    largos = [note(i * 0.5, 36) for i in range(200)]
    pistas = [source_track("drums", "drums", largos)]

    con = make_prepared(pistas, options=pipeline.Options(subdivision=4,
                                                         star_power=True,
                                                         cleanup=False))
    sin = make_prepared(pistas, options=pipeline.Options(subdivision=4,
                                                         star_power=False,
                                                         cleanup=False))

    song_con, _, _ = pipeline.build_song(con, {"drums": ["drums"]})
    song_sin, _, _ = pipeline.build_song(sin, {"drums": ["drums"]})

    assert song_con.track("drums").star_power
    assert song_sin.track("drums").star_power == []


# --------------------------------------------------------------------------
# Eventos preparados una sola vez
# --------------------------------------------------------------------------

def test_the_sections_and_lyrics_of_the_prepared_end_up_in_every_song(make_prepared,
                                                                     pistas):
    """Dependen del tempo, no de la asignacion: se calculan una vez y se copian."""
    from chartgen.ir import Event

    prepared = make_prepared(pistas)
    prepared.section_events = [Event.section(ORIGEN_EN_TICKS, "Intro")]
    prepared.lyric_events = [Event.lyric(ORIGEN_EN_TICKS, "la")]

    primera, _, _ = pipeline.build_song(prepared, {"guitar": ["voice"]})
    segunda, _, _ = pipeline.build_song(prepared, {"bass": ["voice"]})

    for song in (primera, segunda):
        assert [e.text for e in song.sections()] == ["section Intro"]
        assert any(e.text == "lyric la" for e in song.events)


def test_build_song_does_not_mutate_the_prepared(make_prepared, pistas):
    """Se llama una vez por variante: si dejara rastro, la segunda saldria mal."""
    prepared = make_prepared(pistas)
    antes = (len(prepared.tracks), len(prepared.section_events),
             [len(t.notes) for t in prepared.tracks])

    pipeline.build_song(prepared, {"guitar": ["voice"], "drums": ["drums"]})
    pipeline.build_song(prepared, {"guitar": ["voice"], "drums": ["drums"]})

    assert (len(prepared.tracks), len(prepared.section_events),
            [len(t.notes) for t in prepared.tracks]) == antes


def test_two_calls_with_the_same_assignment_give_the_same_thing(make_prepared,
                                                               pistas):
    prepared = make_prepared(pistas)
    asignacion = {"guitar": ["voice"], "drums": ["drums"]}

    a, reports_a, _ = pipeline.build_song(prepared, asignacion)
    b, reports_b, _ = pipeline.build_song(prepared, asignacion)

    assert reports_a == reports_b
    for canal in ("guitar", "drums"):
        assert ([(n.tick, n.fret, n.sustain) for n in a.track(canal).notes]
                == [(n.tick, n.fret, n.sustain) for n in b.track(canal).notes])


# --------------------------------------------------------------------------
# Prepared.notes_of
# --------------------------------------------------------------------------

def test_notes_of_complains_about_the_name_it_did_not_find(make_prepared, pistas):
    prepared = make_prepared(pistas)

    assert prepared.notes_of("voice") is pistas[0].notes
    with pytest.raises(pipeline.PipelineError) as exc:
        prepared.notes_of("Voice")          # distingue mayusculas a proposito
    assert "Voice" in str(exc.value)


# --------------------------------------------------------------------------
# density_lines
# --------------------------------------------------------------------------

def test_density_lines_says_so_when_there_is_nothing_to_measure(make_prepared,
                                                                pistas):
    prepared = make_prepared(pistas)
    song, _, _ = pipeline.build_song(prepared, {"guitar": ["voice"]})

    assert pipeline.density_lines(song, 0.0) == [
        "  (sin audio no se puede medir la densidad)"]


def test_density_lines_gives_one_line_per_track(make_prepared, pistas):
    prepared = make_prepared(pistas)
    song, _, _ = pipeline.build_song(prepared, {"guitar": ["voice"],
                                                "drums": ["drums"]})

    lineas = pipeline.density_lines(song, 60.0)

    assert len(lineas) == 2
    assert any("guitar" in l for l in lineas)
    assert any("drums" in l for l in lineas)
    assert all("notas/s" in l for l in lineas)
