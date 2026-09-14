"""El parseo del CLI: de texto de la linea de comandos a asignaciones.

Aqui el valor no esta en el camino feliz sino en los mensajes de error: quien usa
`--assign` esta escribiendo nombres de pista de un transcriptor a mano, y un
"error de sintaxis" pelado no le dice donde mirar. Cada ValueError tiene que
nombrar lo que no entendio y, cuando se puede, lo que si habria entendido.
"""

from __future__ import annotations

import pytest

import hacer_chart
import pipeline
import variants as variants_mod
from conftest import note, source_track


@pytest.fixture
def tracks():
    """Las pistas del MIDI real, con sus nombres y su clasificacion."""
    return [
        source_track("voice", "vocals", [note(0.0, 60)]),
        source_track("electric_piano", "keys", [note(0.0, 64)]),
        source_track("electric_bass", "bass", [note(0.0, 40)]),
        source_track("violin", None, [note(0.0, 72)]),
        source_track("drums", "drums", [note(0.0, 36)]),
    ]


# --------------------------------------------------------------------------
# resolve_track
# --------------------------------------------------------------------------

def test_a_track_is_reachable_by_its_own_name(tracks):
    assert hacer_chart.resolve_track("electric_bass", tracks) == "electric_bass"


def test_a_track_is_reachable_by_its_one_based_index(tracks):
    """1-based porque es lo que se ve en la lista que imprime la TUI."""
    assert hacer_chart.resolve_track("1", tracks) == "voice"
    assert hacer_chart.resolve_track("5", tracks) == "drums"


def test_a_track_is_reachable_by_the_instrument_that_was_deduced(tracks):
    """`bass` es el instrumento deducido de la pista `electric_bass`."""
    assert hacer_chart.resolve_track("bass", tracks) == "electric_bass"
    assert hacer_chart.resolve_track("vocals", tracks) == "voice"


def test_the_name_wins_over_the_deduced_instrument(tracks):
    """Una pista llamada `bass` gana a la que solo se DEDUCE `bass`.

    Y gana esté donde esté en la lista: la busqueda por nombre recorre todas las
    pistas antes de mirar un solo instrumento deducido. Si no fuera asi, el orden
    de las pistas del archivo decidiria a que se refiere el usuario.
    """
    detras = tracks + [source_track("bass", None, [note(0.0, 40)])]
    delante = [source_track("bass", None, [note(0.0, 40)])] + tracks

    assert hacer_chart.resolve_track("bass", detras) == "bass"
    assert hacer_chart.resolve_track("bass", delante) == "bass"
    # Sin esa pista, `bass` es el instrumento deducido de electric_bass.
    assert hacer_chart.resolve_track("bass", tracks) == "electric_bass"


def test_the_lookup_ignores_case_and_surrounding_spaces(tracks):
    assert hacer_chart.resolve_track("  Electric_Bass ", tracks) == "electric_bass"
    assert hacer_chart.resolve_track("DRUMS", tracks) == "drums"


def test_a_track_the_classifier_did_not_place_is_still_reachable(tracks):
    """Es la razon de conservar el nombre original: rescatar el violin."""
    assert hacer_chart.resolve_track("violin", tracks) == "violin"
    assert hacer_chart.resolve_track("4", tracks) == "violin"


@pytest.mark.parametrize("indice", ["0", "6", "99"])
def test_an_index_out_of_range_says_how_many_there_are(tracks, indice):
    with pytest.raises(ValueError) as exc:
        hacer_chart.resolve_track(indice, tracks)

    mensaje = str(exc.value)
    assert "fuera de rango" in mensaje
    assert indice in mensaje
    assert "hay 5" in mensaje


def test_an_unknown_name_lists_the_ones_there_are(tracks):
    with pytest.raises(ValueError) as exc:
        hacer_chart.resolve_track("trompeta", tracks)

    mensaje = str(exc.value)
    assert "trompeta" in mensaje
    for nombre in ("voice", "electric_bass", "violin", "drums"):
        assert nombre in mensaje


# --------------------------------------------------------------------------
# parse_assign
# --------------------------------------------------------------------------

def test_parse_assign_reads_the_documented_form(tracks):
    assert hacer_chart.parse_assign("voice=guitar,electric_bass=bass",
                                    tracks) == {"voice": "guitar",
                                                "electric_bass": "bass"}


def test_parse_assign_accepts_indexes_and_deduced_names_too(tracks):
    assert hacer_chart.parse_assign("1=guitar,bass=bass,5=drums",
                                    tracks) == {"voice": "guitar",
                                                "electric_bass": "bass",
                                                "drums": "drums"}


def test_parse_assign_tolerates_spaces_and_empty_pieces(tracks):
    assert hacer_chart.parse_assign("  voice = guitar , , drums=drums ,",
                                    tracks) == {"voice": "guitar",
                                                "drums": "drums"}


def test_parse_assign_normalizes_the_channel_to_lowercase(tracks):
    assert hacer_chart.parse_assign("voice=GUITAR", tracks) == {"voice": "guitar"}


def test_parse_assign_lets_two_tracks_share_a_channel(tracks):
    """Se suman, y lo que lo convierte en una lista es `variants.single`."""
    asignacion = hacer_chart.parse_assign(
        "electric_piano=keys,violin=keys", tracks)

    assert asignacion == {"electric_piano": "keys", "violin": "keys"}
    assert variants_mod.single(asignacion).assignment == {
        "keys": ["electric_piano", "violin"]}


def test_parse_assign_refuses_an_unknown_channel_and_lists_the_good_ones(tracks):
    with pytest.raises(ValueError) as exc:
        hacer_chart.parse_assign("voice=vocals", tracks)

    mensaje = str(exc.value)
    assert "--assign" in mensaje
    assert "vocals" in mensaje
    for channel in pipeline.CHANNELS:
        assert channel in mensaje


def test_parse_assign_refuses_a_pair_with_no_equals_sign(tracks):
    with pytest.raises(ValueError) as exc:
        hacer_chart.parse_assign("voice=guitar,electric_bass", tracks)

    mensaje = str(exc.value)
    assert "falta el '='" in mensaje
    assert "electric_bass" in mensaje


@pytest.mark.parametrize("raw", ["", "   ", ",", " , , "])
def test_parse_assign_refuses_an_empty_assignment(tracks, raw):
    with pytest.raises(ValueError) as exc:
        hacer_chart.parse_assign(raw, tracks)

    assert "--assign esta vacio" in str(exc.value)


def test_parse_assign_propagates_the_error_of_an_unknown_track(tracks):
    with pytest.raises(ValueError) as exc:
        hacer_chart.parse_assign("trompeta=guitar", tracks)

    assert "trompeta" in str(exc.value)


def test_parse_assign_propagates_the_error_of_an_index_out_of_range(tracks):
    with pytest.raises(ValueError) as exc:
        hacer_chart.parse_assign("9=guitar", tracks)

    assert "fuera de rango" in str(exc.value)


def test_the_last_channel_wins_when_a_track_is_named_twice(tracks):
    """Una pista no puede ir a dos canales por esta via: la clave es la pista."""
    assert hacer_chart.parse_assign("voice=guitar,voice=bass",
                                    tracks) == {"voice": "bass"}


# --------------------------------------------------------------------------
# parse_candidates
# --------------------------------------------------------------------------

def test_parse_candidates_reads_the_documented_form(tracks):
    assert hacer_chart.parse_candidates(
        "guitar=voice|electric_bass,drums=drums", tracks) == {
            "guitar": ["voice", "electric_bass"], "drums": ["drums"]}


def test_the_pipe_is_what_separates_candidates_of_one_channel(tracks):
    choices = hacer_chart.parse_candidates(
        "guitar=voice|electric_piano|violin", tracks)

    assert choices == {"guitar": ["voice", "electric_piano", "violin"]}


def test_a_zero_among_the_candidates_is_the_empty_option(tracks):
    choices = hacer_chart.parse_candidates("keys=electric_piano|0", tracks)

    assert choices == {"keys": ["electric_piano", variants_mod.EMPTY]}
    assert variants_mod.EMPTY is None


def test_the_empty_option_reaches_the_expansion(tracks):
    """El 0 no es decorativo: tiene que producir variantes sin ese canal."""
    choices = hacer_chart.parse_candidates("guitar=voice,keys=electric_piano|0",
                                           tracks)
    expansion = variants_mod.expand(choices)

    assert [x.by_channel for x in expansion.variants] == [
        {"guitar": "voice", "keys": "electric_piano"},
        {"guitar": "voice"},
    ]


def test_parse_candidates_accepts_indexes_and_deduced_names(tracks):
    choices = hacer_chart.parse_candidates("guitar=1|bass,drums=5", tracks)

    assert choices == {"guitar": ["voice", "electric_bass"], "drums": ["drums"]}


def test_parse_candidates_drops_repeated_candidates(tracks):
    """Dos veces la misma pista en un canal solo daria dos carpetas iguales."""
    choices = hacer_chart.parse_candidates("guitar=voice|1|voice", tracks)

    assert choices == {"guitar": ["voice"]}


def test_parse_candidates_drops_a_repeated_empty_option(tracks):
    choices = hacer_chart.parse_candidates("keys=0|0|electric_piano", tracks)

    assert choices == {"keys": [variants_mod.EMPTY, "electric_piano"]}


def test_parse_candidates_tolerates_spaces_and_empty_pieces(tracks):
    choices = hacer_chart.parse_candidates(
        " guitar = voice | | electric_bass , , drums = drums ", tracks)

    assert choices == {"guitar": ["voice", "electric_bass"], "drums": ["drums"]}


def test_parse_candidates_normalizes_the_channel_to_lowercase(tracks):
    assert hacer_chart.parse_candidates("GUITAR=voice", tracks) == {
        "guitar": ["voice"]}


def test_parse_candidates_refuses_an_unknown_channel(tracks):
    with pytest.raises(ValueError) as exc:
        hacer_chart.parse_candidates("vocals=voice", tracks)

    mensaje = str(exc.value)
    assert "--candidates" in mensaje
    assert "vocals" in mensaje
    for channel in pipeline.CHANNELS:
        assert channel in mensaje


def test_parse_candidates_refuses_a_group_with_no_equals_sign(tracks):
    with pytest.raises(ValueError) as exc:
        hacer_chart.parse_candidates("guitar=voice,drums", tracks)

    mensaje = str(exc.value)
    assert "falta el '='" in mensaje
    assert "drums" in mensaje


@pytest.mark.parametrize("raw", ["", "   ", ",", "guitar=", "guitar=|"])
def test_parse_candidates_refuses_an_empty_set_of_choices(tracks, raw):
    with pytest.raises(ValueError) as exc:
        hacer_chart.parse_candidates(raw, tracks)

    assert "--candidates esta vacio" in str(exc.value)


def test_parse_candidates_propagates_the_error_of_an_unknown_track(tracks):
    with pytest.raises(ValueError) as exc:
        hacer_chart.parse_candidates("guitar=voice|trompeta", tracks)

    assert "trompeta" in str(exc.value)


def test_the_last_group_wins_when_a_channel_is_named_twice(tracks):
    assert hacer_chart.parse_candidates("guitar=voice,guitar=violin",
                                        tracks) == {"guitar": ["violin"]}


# --------------------------------------------------------------------------
# build_parser y options_from
# --------------------------------------------------------------------------

def parse(argv):
    return hacer_chart.build_parser().parse_args(argv)


def test_the_parser_needs_at_least_one_midi():
    with pytest.raises(SystemExit):
        parse([])


def test_the_parser_takes_several_midis():
    args = parse(["a.mid", "b.mid"])

    assert [p.name for p in args.midi] == ["a.mid", "b.mid"]


def test_the_defaults_are_the_documented_ones():
    args = parse(["a.mid"])

    assert args.detector == "auto"
    assert args.format == "chart"
    assert args.max_variants == variants_mod.MAX_VARIANTS
    assert args.out == hacer_chart.DEFAULT_OUT
    assert args.assign is None and args.candidates is None


def test_options_from_turns_the_no_flags_into_falses():
    options = hacer_chart.options_from(
        parse(["a.mid", "--no-metadata", "--no-art", "--no-preview",
               "--no-cleanup", "--no-star-power"]))

    assert options.lookup_metadata is False
    assert options.fetch_art is False
    assert options.find_preview is False
    assert options.cleanup is False
    assert options.star_power is False


def test_options_from_leaves_everything_on_by_default():
    options = hacer_chart.options_from(parse(["a.mid"]))

    assert options.lookup_metadata is True
    assert options.fetch_art is True
    assert options.find_preview is True
    assert options.cleanup is True
    assert options.star_power is True
    assert options.formats == ("chart",)


@pytest.mark.parametrize("flag,formats", [
    ("chart", ("chart",)),
    ("mid", ("mid",)),
    ("both", ("chart", "mid")),
])
def test_the_format_flag_becomes_the_tuple_the_packager_expects(flag, formats):
    options = hacer_chart.options_from(parse(["a.mid", "--format", flag]))

    assert options.formats == formats


def test_force_is_what_lets_a_folder_be_overwritten():
    assert hacer_chart.options_from(parse(["a.mid"])).overwrite is False
    assert hacer_chart.options_from(parse(["a.mid", "--force"])).overwrite is True


def test_the_metadata_written_by_hand_reaches_the_options():
    options = hacer_chart.options_from(
        parse(["a.mid", "--title", "T", "--artist", "A", "--album", "Al",
               "--genre", "G", "--year", "2024", "--charter", "yo"]))

    assert (options.title, options.artist, options.album) == ("T", "A", "Al")
    assert (options.genre, options.year, options.charter) == ("G", "2024", "yo")


def test_a_subdivision_outside_the_known_grids_is_refused_by_the_parser():
    from chartgen.frontends import transcription as tx_frontend

    assert 5 not in tx_frontend.SUBDIVISIONS
    with pytest.raises(SystemExit):
        parse(["a.mid", "--subdivision", "5"])
    assert parse(["a.mid", "--subdivision", "4"]).subdivision == 4


def test_audio_with_several_midis_is_refused_before_doing_any_work(capsys):
    codigo = hacer_chart.main(["a.mid", "b.mid", "--audio", "x.mp3"])

    assert codigo == 1
    assert "--audio solo vale con un MIDI" in capsys.readouterr().out
