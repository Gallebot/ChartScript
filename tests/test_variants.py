"""Combinatoria de asignaciones canal-del-chart -> pistas-del-MIDI.

Lo que se comprueba es el contrato que el modulo declara en su docstring: el
producto cartesiano sale completo, lo que se descarta se CUENTA en vez de
desaparecer, `EMPTY` es un candidato mas y no una excepcion, y la asignacion va
de canal a LISTA de pistas para que una pista pueda alimentar dos canales.
"""

from __future__ import annotations

import variants as v


def repartos(expansion) -> list[dict[str, str]]:
    """Los repartos de una expansion en su forma legible (canal -> texto)."""
    return [x.by_channel for x in expansion.variants]


# --------------------------------------------------------------------------
# Producto cartesiano
# --------------------------------------------------------------------------

def test_the_cartesian_product_comes_out_whole():
    expansion = v.expand({"guitar": ["voice", "electric_bass"],
                          "drums": ["drums"]})

    assert repartos(expansion) == [
        {"guitar": "voice", "drums": "drums"},
        {"guitar": "electric_bass", "drums": "drums"},
    ]
    assert (expansion.skipped_reuse, expansion.skipped_duplicate) == (0, 0)


def test_every_combination_of_three_channels_appears_once():
    choices = {"guitar": ["a", "b", "c"], "bass": ["d", "e"], "drums": ["f"]}
    expansion = v.expand(choices)

    assert len(expansion.variants) == 3 * 2 * 1
    assert len({tuple(sorted(x.by_channel.items()))
                for x in expansion.variants}) == 6


def test_the_assignment_maps_a_channel_to_the_list_of_its_tracks():
    """La direccion que come `pipeline.build_song`: canal -> [pistas]."""
    variant = v.expand({"guitar": ["voice"], "bass": ["electric_bass"]}).variants[0]

    assert variant.assignment == {"guitar": ["voice"], "bass": ["electric_bass"]}
    assert variant.by_channel == {"guitar": "voice", "bass": "electric_bass"}


# --------------------------------------------------------------------------
# EMPTY
# --------------------------------------------------------------------------

def test_empty_leaves_the_channel_out_instead_of_naming_it():
    expansion = v.expand({"guitar": ["voice"], "keys": [v.EMPTY, "synth_pad"]})

    assert repartos(expansion) == [
        {"guitar": "voice"},
        {"guitar": "voice", "keys": "synth_pad"},
    ]
    # El canal vacio no aparece con None dentro: simplemente no esta.
    for variant in expansion.variants:
        assert "keys" not in variant.assignment or variant.assignment["keys"]
        assert all(None not in tracks for tracks in variant.assignment.values())


def test_the_combination_that_empties_every_channel_produces_no_variant():
    """Sin ningun canal no hay chart, y eso no es un descarte que contar."""
    expansion = v.expand({"guitar": [v.EMPTY], "bass": [v.EMPTY]})

    assert expansion.variants == []
    assert (expansion.skipped_reuse, expansion.skipped_duplicate) == (0, 0)


def test_empty_is_a_candidate_like_any_other_and_multiplies():
    expansion = v.expand({"guitar": ["voice", v.EMPTY],
                          "bass": ["electric_bass", v.EMPTY]})

    # 4 combinaciones - la que deja todo vacio = 3.
    assert repartos(expansion) == [
        {"guitar": "voice", "bass": "electric_bass"},
        {"guitar": "voice"},
        {"bass": "electric_bass"},
    ]


# --------------------------------------------------------------------------
# Reuso de una pista en dos canales
# --------------------------------------------------------------------------

def test_reusing_a_track_in_two_channels_is_dropped_and_counted():
    """El caso que el docstring llama el que mas confunde: entran 2 combinaciones
    y sale 1, y el usuario tiene que poder enterarse."""
    expansion = v.expand({"guitar": ["voice", "electric_bass"],
                          "bass": ["electric_bass"]})

    assert repartos(expansion) == [{"guitar": "voice", "bass": "electric_bass"}]
    assert expansion.skipped_reuse == 1
    assert expansion.skipped_duplicate == 0


def test_what_was_dropped_by_reuse_is_told_and_points_at_the_flag():
    notes = v.expand({"guitar": ["voice", "electric_bass"],
                      "bass": ["electric_bass"]}).notes()

    assert len(notes) == 1
    assert "--allow-reuse" in notes[0]


def test_allow_reuse_keeps_them():
    expansion = v.expand({"guitar": ["voice", "electric_bass"],
                          "bass": ["electric_bass"]}, allow_reuse=True)

    assert repartos(expansion) == [
        {"guitar": "voice", "bass": "electric_bass"},
        {"guitar": "electric_bass", "bass": "electric_bass"},
    ]
    assert expansion.skipped_reuse == 0
    assert expansion.notes() == []


def test_allow_reuse_actually_reaches_both_channels():
    """Lo que promete el docstring: la misma linea en guitarra y bajo a la vez.

    Es la razon por la que `assignment` va de canal a lista y no de pista a canal:
    en la otra direccion la pista repetida se pisaba y un canal desaparecia del
    chart sin avisar, aunque la etiqueta de la carpeta lo siguiera nombrando.
    """
    variant = v.expand({"guitar": ["voice"], "bass": ["voice"]},
                       allow_reuse=True).variants[0]

    assert variant.assignment == {"guitar": ["voice"], "bass": ["voice"]}
    assert variant.by_channel == {"guitar": "voice", "bass": "voice"}


# --------------------------------------------------------------------------
# Duplicados exactos
# --------------------------------------------------------------------------

def test_exact_duplicates_are_unified_and_counted():
    """Un candidato repetido en la misma lista daria dos carpetas identicas."""
    expansion = v.expand({"guitar": ["voice", "voice"], "drums": ["drums"]})

    assert repartos(expansion) == [{"guitar": "voice", "drums": "drums"}]
    assert expansion.skipped_duplicate == 1
    assert expansion.skipped_reuse == 0
    assert "duplicados exactos" in expansion.notes()[0]


def test_all_the_duplicates_of_a_reparto_are_counted_not_just_the_first():
    expansion = v.expand({"guitar": ["voice", "voice", "voice"],
                          "bass": ["electric_bass", "electric_bass"]})

    assert len(expansion.variants) == 1
    assert expansion.skipped_duplicate == 5


# --------------------------------------------------------------------------
# Entradas degeneradas
# --------------------------------------------------------------------------

def test_no_choices_at_all_gives_an_empty_expansion_without_blowing_up():
    for choices in ({}, {"guitar": []}, {"guitar": [], "bass": []}):
        expansion = v.expand(choices)
        assert isinstance(expansion, v.Expansion)
        assert expansion.variants == []
        assert expansion.notes() == []


def test_a_channel_with_no_candidates_does_not_cancel_the_others():
    expansion = v.expand({"guitar": ["voice"], "keys": []})

    assert repartos(expansion) == [{"guitar": "voice"}]


# --------------------------------------------------------------------------
# Etiquetas
# --------------------------------------------------------------------------

def test_the_label_only_names_the_channels_that_vary():
    """Si las dos variantes comparten drums, ponerlo en las dos no distingue."""
    choices = {"guitar": ["voice", "electric_bass"], "drums": ["drums"]}
    expansion = v.expand(choices)
    varying = v.varying_channels(choices)

    assert varying == ("guitar",)
    assert [x.label(varying) for x in expansion.variants] == [
        "[gtr=voice]", "[gtr=electric_bass]"]


def test_without_varying_channels_the_label_names_everything():
    variant = v.expand({"guitar": ["voice"], "drums": ["drums"]}).variants[0]

    assert variant.label() == "[gtr=voice, drums=drums]"


def test_a_varying_channel_the_variant_does_not_have_is_skipped_in_the_label():
    """Con `keys` variando entre EMPTY y una pista, la variante sin keys no
    puede nombrarlo, pero tampoco debe romper la etiqueta."""
    choices = {"guitar": ["voice"], "keys": [v.EMPTY, "synth_pad"]}
    expansion = v.expand(choices)
    varying = v.varying_channels(choices)

    assert varying == ("keys",)
    assert [x.label(varying) for x in expansion.variants] == ["",
                                                             "[keys=synth_pad]"]


def test_the_short_names_are_the_ones_used_in_the_label():
    variant = v.single({"electric_bass": "rhythm"})

    assert v.SHORT["rhythm"] == "rhy"
    assert variant.label() == "[rhy=electric_bass]"


def test_a_channel_with_no_short_name_falls_back_to_its_own_name():
    variant = v.Variant(assignment={"vocals": ["voice"]})

    assert "vocals" not in v.SHORT
    assert variant.label() == "[vocals=voice]"


def test_describe_is_sorted_by_channel_so_two_variants_compare_by_eye():
    variant = v.single({"drums": "drums", "voice": "guitar",
                        "electric_bass": "bass"})

    assert variant.describe() == "bass=electric_bass, drums=drums, guitar=voice"


# --------------------------------------------------------------------------
# single(): el modo manual, donde sumar SI se permite
# --------------------------------------------------------------------------

def test_single_lets_several_tracks_share_a_channel():
    """Es como se juntan dos teclados en `keys`, y lo ha pedido el usuario."""
    variant = v.single({"electric_piano": "keys", "synth_pad": "keys"})

    assert variant.assignment == {"keys": ["electric_piano", "synth_pad"]}
    assert variant.by_channel == {"keys": "electric_piano+synth_pad"}


def test_single_concatenates_in_the_order_it_was_given():
    variant = v.single({"synth_pad": "keys", "electric_piano": "keys"})

    assert variant.by_channel == {"keys": "synth_pad+electric_piano"}


def test_single_inverts_the_direction_the_user_writes_it_in():
    variant = v.single({"voice": "guitar", "electric_bass": "bass"})

    assert variant.assignment == {"guitar": ["voice"], "bass": ["electric_bass"]}


def test_single_does_not_alias_the_dict_it_was_given():
    original = {"voice": "guitar"}
    variant = v.single(original)
    original["drums"] = "drums"

    assert variant.assignment == {"guitar": ["voice"]}


def test_single_label_shows_the_summed_channel_as_one_entry():
    variant = v.single({"electric_piano": "keys", "synth_pad": "keys",
                        "drums": "drums"})

    assert variant.label() == "[keys=electric_piano+synth_pad, drums=drums]"


def test_single_accepts_an_empty_assignment_without_blowing_up():
    """Quien llama es el que decide si una variante vacia es un error."""
    variant = v.single({})

    assert variant.assignment == {}
    assert variant.label() == ""
    assert variant.describe() == ""


# --------------------------------------------------------------------------
# varying_channels y el tope
# --------------------------------------------------------------------------

def test_varying_channels_ignores_the_channels_with_a_single_candidate():
    choices = {"guitar": ["a", "b"], "bass": ["c"], "keys": ["d", "e", "f"]}

    assert v.varying_channels(choices) == ("guitar", "keys")


def test_expand_does_not_apply_the_cap_itself():
    """`MAX_VARIANTS` es para que quien llama avise antes, no un limite de expand."""
    choices = {c: ["a", "b", "c"] for c in ("guitar", "bass", "rhythm")}
    expansion = v.expand(choices, allow_reuse=True)

    assert v.MAX_VARIANTS == 24
    assert len(expansion.variants) == 27
