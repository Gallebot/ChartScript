"""`find_audio`: el audio hermano del MIDI.

La convencion es "el mismo nombre, en la misma carpeta". Lo que hay que fijar con
tests es el ORDEN de preferencia, porque no es arbitrario: `.ogg` va primero
porque es el formato que acaba en la carpeta de Clone Hero y saltarse la
reconversion evita una generacion de perdida mas.
"""

from __future__ import annotations

import pytest

import pipeline


def crear(carpeta, nombre: str) -> None:
    (carpeta / nombre).write_bytes(b"no es audio de verdad, pero existe")


def test_it_finds_the_sibling_with_the_same_stem(tmp_path):
    midi = tmp_path / "DANNA - SI SE ACABA EL MUNDO.mid"
    crear(tmp_path, midi.name)
    crear(tmp_path, "DANNA - SI SE ACABA EL MUNDO.mp3")

    assert pipeline.find_audio(midi) == tmp_path / "DANNA - SI SE ACABA EL MUNDO.mp3"


def test_without_any_audio_it_returns_none(tmp_path):
    midi = tmp_path / "solo.mid"
    crear(tmp_path, midi.name)

    assert pipeline.find_audio(midi) is None


def test_an_audio_with_a_different_stem_is_not_its_sibling(tmp_path):
    midi = tmp_path / "cancion.mid"
    crear(tmp_path, midi.name)
    crear(tmp_path, "otra cancion.mp3")

    assert pipeline.find_audio(midi) is None


def test_an_audio_in_another_folder_is_not_its_sibling(tmp_path):
    midi = tmp_path / "cancion.mid"
    crear(tmp_path, midi.name)
    (tmp_path / "audio").mkdir()
    crear(tmp_path / "audio", "cancion.mp3")

    assert pipeline.find_audio(midi) is None


# --------------------------------------------------------------------------
# Orden de preferencia
# --------------------------------------------------------------------------

def test_ogg_wins_over_everything_else(tmp_path):
    midi = tmp_path / "cancion.mid"
    crear(tmp_path, midi.name)
    for ext in pipeline.AUDIO_EXTENSIONS:
        crear(tmp_path, f"cancion{ext}")

    assert pipeline.find_audio(midi).suffix == ".ogg"


@pytest.mark.parametrize("presentes,esperado", [
    ((".mp3", ".ogg"), ".ogg"),
    ((".wav", ".mp3"), ".mp3"),
    ((".flac", ".m4a"), ".m4a"),
    ((".flac", ".wav"), ".wav"),
    ((".opus", ".m4a"), ".opus"),
    ((".flac",), ".flac"),
])
def test_the_preference_order_is_the_one_the_constant_declares(tmp_path, presentes,
                                                               esperado):
    midi = tmp_path / "cancion.mid"
    crear(tmp_path, midi.name)
    for ext in presentes:
        crear(tmp_path, f"cancion{ext}")

    assert pipeline.find_audio(midi).suffix == esperado


def test_every_declared_extension_is_findable_on_its_own(tmp_path):
    """Si alguna de las seis no se encontrara, la constante mentiria."""
    for ext in pipeline.AUDIO_EXTENSIONS:
        carpeta = tmp_path / ext.lstrip(".")
        carpeta.mkdir()
        midi = carpeta / "cancion.mid"
        crear(carpeta, midi.name)
        crear(carpeta, f"cancion{ext}")

        assert pipeline.find_audio(midi) == carpeta / f"cancion{ext}"


def test_the_order_of_the_constant_is_the_documented_one():
    assert pipeline.AUDIO_EXTENSIONS[0] == ".ogg"
    assert set(pipeline.AUDIO_EXTENSIONS) == {".ogg", ".mp3", ".opus", ".m4a",
                                              ".wav", ".flac"}


# --------------------------------------------------------------------------
# Nombres con puntos
# --------------------------------------------------------------------------

def test_a_name_with_dots_in_it_keeps_the_whole_stem(tmp_path):
    """'Artista - Titulo (feat. Alguien).mid' no debe perder el '(feat.'."""
    midi = tmp_path / "Artista - Titulo (feat. Alguien).mid"
    crear(tmp_path, midi.name)
    crear(tmp_path, "Artista - Titulo (feat. Alguien).mp3")

    assert pipeline.find_audio(midi).name == "Artista - Titulo (feat. Alguien).mp3"


def test_the_real_example_finds_its_mp3(real_midi):
    """El unico test que mira los ejemplos: comprueba que existen y se ven.

    No lee el mp3 ni lo analiza, asi que no cuesta nada.
    """
    audio = pipeline.find_audio(real_midi)

    assert audio is not None
    assert audio.suffix == ".mp3"
    assert audio.stem == real_midi.stem


def test_the_copy_without_its_mp3_finds_nothing(lonely_midi):
    """La base de casi toda la suite: sin audio al lado, el tempo sale del MIDI."""
    assert pipeline.find_audio(lonely_midi) is None
