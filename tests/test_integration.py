"""Integracion: del MIDI real a una carpeta que Clone Hero puede escanear.

Todo lo de aqui usa el MIDI de ejemplo (6 pistas, 2.114 notas) pero SIN su mp3 al
lado: el tempo sale del propio MIDI y la suite no paga el minuto y medio que
cuesta detectar beats. El unico test que toca audio real lleva `@pytest.mark.slow`
y no se ejecuta en la corrida normal (ver `pytest.ini`).
"""

from __future__ import annotations

import configparser

import pytest

import hacer_chart
import pipeline
from chartgen import PAD_SECONDS, RESOLUTION
from chartgen.backends import chart as chart_backend
from chartgen.frontends import midi as midi_frontend
from chartgen.tempo import TempoMap

PISTAS_ESPERADAS = {"voice": 689, "electric_piano": 387, "electric_bass": 339,
                    "violin": 112, "synth_pad": 61, "drums": 526}


@pytest.fixture
def prepared_del_midi_real(real_midi, tmp_path):
    """`Prepared` real salvo el mapa de tempo, que se fija constante.

    Un mapa constante basta para lo que se comprueba aqui (que las pistas salgan y
    que el backend escriba) y hace el test determinista y rapido.
    """
    return pipeline.Prepared(
        midi=real_midi,
        audio=None,
        tracks=midi_frontend.read_tracks(real_midi),
        tempo_map=TempoMap.constant(120),
        origin_tick=4 * RESOLUTION,
        tempo_note="constante para el test",
        tempo_reliable=True,
        metadata=pipeline.Metadata(name="SI SE ACABA EL MUNDO", artist="DANNA"),
        art=None,
        preview_start=PAD_SECONDS,
        duration=180.0,
        options=pipeline.Options(lookup_metadata=False, fetch_art=False,
                                 find_preview=False),
    )


# --------------------------------------------------------------------------
# Lo que trae el MIDI de ejemplo
# --------------------------------------------------------------------------

def test_the_example_midi_has_the_tracks_the_tests_assume(real_midi):
    tracks = midi_frontend.read_tracks(real_midi)

    assert {t.name: len(t.notes) for t in tracks} == PISTAS_ESPERADAS


def test_the_classifier_places_what_it_can_and_leaves_the_violin_out(real_midi):
    tracks = {t.name: t.instrument for t in midi_frontend.read_tracks(real_midi)}

    assert tracks == {"voice": "vocals", "electric_piano": "keys",
                      "electric_bass": "bass", "violin": None,
                      "synth_pad": "keys", "drums": "drums"}
    # `vocals` no es un canal que ningun backend de aqui escriba.
    assert "vocals" not in pipeline.CHANNELS


# --------------------------------------------------------------------------
# build_song sobre el MIDI real
# --------------------------------------------------------------------------

def test_build_song_produces_the_expected_tracks(prepared_del_midi_real):
    song, reports, warnings = pipeline.build_song(
        prepared_del_midi_real,
        {"guitar": ["voice"], "bass": ["electric_bass"],
         "keys": ["electric_piano", "synth_pad"], "drums": ["drums"]})

    assert [t.instrument for t in song.tracks] == ["guitar", "bass", "keys",
                                                   "drums"]
    assert sorted(reports) == ["bass", "drums", "guitar", "keys"]
    assert all(t.notes for t in song.tracks)
    # Hay guitarra, asi que no debe salir el aviso de la cancion vacia.
    assert not any("no hay canal de guitarra" in w for w in warnings)


def test_every_note_of_every_track_lands_after_the_lead_in(prepared_del_midi_real):
    """Nada puede caer antes del origen: seria musica dentro del silencio."""
    song, _, _ = pipeline.build_song(
        prepared_del_midi_real, {"guitar": ["voice"], "drums": ["drums"]})

    for track in song.tracks:
        assert min(n.tick for n in track.notes) >= prepared_del_midi_real.origin_tick


def test_the_rescued_violin_can_be_the_guitar_channel(prepared_del_midi_real):
    """Lo que justifica conservar el nombre de una pista sin clasificar."""
    song, reports, _ = pipeline.build_song(prepared_del_midi_real,
                                           {"guitar": ["violin"]})

    assert song.track("guitar").notes
    assert "encajadas" in reports["guitar"]


def test_the_two_keyboards_summed_give_more_notes_than_either_alone(
        prepared_del_midi_real):
    solo, _, _ = pipeline.build_song(prepared_del_midi_real,
                                     {"keys": ["electric_piano"]})
    sumado, _, _ = pipeline.build_song(
        prepared_del_midi_real, {"keys": ["electric_piano", "synth_pad"]})

    assert len(sumado.track("keys").notes) > len(solo.track("keys").notes)


# --------------------------------------------------------------------------
# El backend .chart
# --------------------------------------------------------------------------

def test_the_chart_backend_writes_the_real_song_without_blowing_up(
        prepared_del_midi_real, tmp_path):
    song, _, _ = pipeline.build_song(
        prepared_del_midi_real,
        {"guitar": ["voice"], "bass": ["electric_bass"], "drums": ["drums"]})

    texto = chart_backend.render(song)
    destino = chart_backend.write(song, tmp_path / "notes.chart")

    assert destino.exists()
    # newline="" para leer los CRLF tal cual: sin eso Python los traduce y el
    # test compararia otra cosa que la que se escribio.
    with destino.open(encoding="utf-8", newline="") as f:
        assert f.read() == texto


def test_the_written_chart_has_the_sections_the_game_looks_for(
        prepared_del_midi_real):
    song, _, _ = pipeline.build_song(
        prepared_del_midi_real,
        {"guitar": ["voice"], "bass": ["electric_bass"], "drums": ["drums"]})

    texto = chart_backend.render(song)

    for seccion in ("[Song]", "[SyncTrack]", "[Events]", "[ExpertSingle]",
                    "[ExpertDoubleBass]", "[ExpertDrums]"):
        assert seccion in texto
    assert f"Resolution = {RESOLUTION}" in texto
    assert 'MusicStream = "song.ogg"' in texto


def test_the_chart_keeps_the_crlf_moonscraper_writes(prepared_del_midi_real):
    song, _, _ = pipeline.build_song(prepared_del_midi_real,
                                     {"guitar": ["voice"]})

    texto = chart_backend.render(song)

    assert "\r\n" in texto
    assert "\n" not in texto.replace("\r\n", "")


def test_the_cymbal_markers_of_the_drums_reach_the_chart(prepared_del_midi_real):
    """66/67/68 en el `.chart` marcan platillo; son notas extra del mismo tick."""
    from chartgen.drums import CYMBAL_MARKER

    song, _, _ = pipeline.build_song(prepared_del_midi_real, {"drums": ["drums"]})
    texto = chart_backend.render(song)

    cuerpo = texto.split("[ExpertDrums]")[1]
    assert any(f"N {marca} 0" in cuerpo for marca in CYMBAL_MARKER.values())


# --------------------------------------------------------------------------
# End to end por el CLI
# --------------------------------------------------------------------------

def test_main_end_to_end_builds_a_folder_the_game_can_scan(lonely_midi, tmp_path,
                                                          ffmpeg, capsys):
    """El camino completo, sin red y sin audio: lo que hace el .bat.

    Sin mp3 hermano `song.ogg` sale en silencio, que es justo lo documentado: el
    chart se puede editar en Moonscraper aunque no se pueda jugar con musica.
    """
    salida = tmp_path / "out"

    codigo = hacer_chart.main([
        str(lonely_midi), "--assign", "voice=guitar,drums=drums",
        "--no-metadata", "--no-art", "--no-preview", "--out", str(salida)])

    assert codigo == 0
    carpetas = [p for p in salida.iterdir() if p.is_dir()]
    assert len(carpetas) == 1
    carpeta = carpetas[0]

    for nombre in ("notes.chart", "song.ini", "song.ogg"):
        archivo = carpeta / nombre
        assert archivo.exists(), nombre
        assert archivo.stat().st_size > 0, nombre

    # Y la consola cuenta lo que hizo con cada canal.
    impreso = capsys.readouterr().out
    assert "guitar" in impreso and "drums" in impreso


def test_the_folder_is_named_artist_minus_title(lonely_midi, tmp_path, ffmpeg):
    """El nombre sale del archivo: 'DANNA - SI SE ACABA EL MUNDO.mid'."""
    salida = tmp_path / "out"

    hacer_chart.main([str(lonely_midi), "--assign", "voice=guitar",
                      "--no-metadata", "--no-art", "--no-preview",
                      "--out", str(salida)])

    assert [p.name for p in salida.iterdir()] == ["DANNA - SI SE ACABA EL MUNDO"]


def test_the_song_ini_carries_what_clone_hero_reads(lonely_midi, tmp_path, ffmpeg):
    salida = tmp_path / "out"
    hacer_chart.main([str(lonely_midi), "--assign", "voice=guitar,drums=drums",
                      "--no-metadata", "--no-art", "--no-preview",
                      "--out", str(salida)])

    ini = next(salida.rglob("song.ini"))
    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")

    seccion = parser["Song"]
    assert seccion["name"] == "SI SE ACABA EL MUNDO"
    assert seccion["artist"] == "DANNA"
    assert int(seccion["song_length"]) > 0
    # Los canales generados se marcan con dificultad, los demas quedan en -1.
    assert seccion["diff_guitar"] == "3"
    assert seccion["diff_drums"] == "3"
    assert seccion["diff_vocals"] == "-1"


def test_the_written_chart_is_readable_back_as_text(lonely_midi, tmp_path, ffmpeg):
    salida = tmp_path / "out"
    hacer_chart.main([str(lonely_midi), "--assign", "voice=guitar",
                      "--no-metadata", "--no-art", "--no-preview",
                      "--out", str(salida)])

    texto = next(salida.rglob("notes.chart")).read_text(encoding="utf-8")

    assert texto.startswith("[Song]")
    assert '"DANNA"' in texto
    assert "[ExpertSingle]" in texto


def test_running_it_twice_refuses_to_overwrite_and_force_allows_it(lonely_midi,
                                                                  tmp_path,
                                                                  ffmpeg, capsys):
    """Importa cuando `--out` apunta a la biblioteca del usuario."""
    salida = tmp_path / "out"
    comun = ["--assign", "voice=guitar", "--no-metadata", "--no-art",
             "--no-preview", "--out", str(salida)]

    assert hacer_chart.main([str(lonely_midi), *comun]) == 0
    capsys.readouterr()

    assert hacer_chart.main([str(lonely_midi), *comun]) == 1
    assert "Ya existe un chart" in capsys.readouterr().out

    assert hacer_chart.main([str(lonely_midi), *comun, "--force"]) == 0


def test_candidates_generates_one_folder_per_combination(lonely_midi, tmp_path,
                                                         ffmpeg):
    salida = tmp_path / "out"

    codigo = hacer_chart.main([
        str(lonely_midi), "--candidates", "guitar=voice|violin,drums=drums",
        "--no-metadata", "--no-art", "--no-preview", "--out", str(salida)])

    assert codigo == 0
    nombres = sorted(p.name for p in salida.iterdir())
    assert nombres == ["DANNA - SI SE ACABA EL MUNDO [gtr=violin]",
                       "DANNA - SI SE ACABA EL MUNDO [gtr=voice]"]


def test_the_variant_suffix_also_goes_into_the_title(lonely_midi, tmp_path, ffmpeg):
    """Si no, dos variantes de la misma cancion salen iguales en el juego."""
    salida = tmp_path / "out"
    hacer_chart.main([str(lonely_midi), "--candidates", "guitar=voice|violin",
                      "--no-metadata", "--no-art", "--no-preview",
                      "--out", str(salida)])

    titulos = set()
    for ini in salida.rglob("song.ini"):
        parser = configparser.ConfigParser()
        parser.read(ini, encoding="utf-8")
        titulos.add(parser["Song"]["name"])

    assert titulos == {"SI SE ACABA EL MUNDO [gtr=voice]",
                       "SI SE ACABA EL MUNDO [gtr=violin]"}


def test_max_variants_caps_how_many_folders_are_written(lonely_midi, tmp_path,
                                                       ffmpeg, capsys):
    salida = tmp_path / "out"

    codigo = hacer_chart.main([
        str(lonely_midi), "--candidates",
        "guitar=voice|violin|electric_piano", "--max-variants", "1",
        "--no-metadata", "--no-art", "--no-preview", "--out", str(salida)])

    assert codigo == 0
    assert len(list(salida.iterdir())) == 1
    assert "se generan las 1 primeras" in capsys.readouterr().out


def test_list_only_enumerates_the_tracks_and_does_nothing_else(lonely_midi,
                                                               tmp_path, capsys):
    salida = tmp_path / "out"

    codigo = hacer_chart.main([str(lonely_midi), "--list", "--out", str(salida)])

    impreso = capsys.readouterr().out
    assert codigo == 0
    assert "6 pistas con notas" in impreso
    for nombre in PISTAS_ESPERADAS:
        assert nombre in impreso
    assert list(salida.iterdir()) == []


def test_the_mid_format_is_also_writable_end_to_end(lonely_midi, tmp_path, ffmpeg):
    salida = tmp_path / "out"

    codigo = hacer_chart.main([
        str(lonely_midi), "--assign", "voice=guitar,drums=drums",
        "--format", "both", "--no-metadata", "--no-art", "--no-preview",
        "--out", str(salida)])

    assert codigo == 0
    carpeta = next(p for p in salida.iterdir() if p.is_dir())
    assert (carpeta / "notes.chart").exists()
    assert (carpeta / "notes.mid").read_bytes()[:4] == b"MThd"


# --------------------------------------------------------------------------
# Errores del CLI
# --------------------------------------------------------------------------

def test_a_midi_that_does_not_exist_is_an_error_and_not_a_traceback(tmp_path,
                                                                   capsys):
    codigo = hacer_chart.main([str(tmp_path / "no_existe.mid"), "--assign",
                               "voice=guitar", "--out", str(tmp_path / "out")])

    assert codigo == 1
    assert "ERROR" in capsys.readouterr().out


def test_an_unknown_channel_in_assign_is_an_error_and_not_a_traceback(lonely_midi,
                                                                     tmp_path,
                                                                     capsys):
    codigo = hacer_chart.main([str(lonely_midi), "--assign", "voice=vocals",
                               "--no-metadata", "--no-art", "--no-preview",
                               "--out", str(tmp_path / "out")])

    assert codigo == 1
    assert "canal desconocido 'vocals'" in capsys.readouterr().out


def test_a_combination_that_leaves_nothing_is_explained(lonely_midi, tmp_path,
                                                        capsys):
    """Mismo candidato en dos canales sin --allow-reuse: no queda ninguna."""
    codigo = hacer_chart.main([
        str(lonely_midi), "--candidates", "guitar=voice,bass=voice",
        "--no-metadata", "--no-art", "--no-preview",
        "--out", str(tmp_path / "out")])

    assert codigo == 1
    salida = capsys.readouterr().out
    assert "--allow-reuse" in salida


def test_allow_reuse_gets_that_same_combination_through(lonely_midi, tmp_path,
                                                        ffmpeg):
    salida = tmp_path / "out"

    codigo = hacer_chart.main([
        str(lonely_midi), "--candidates", "guitar=voice,bass=voice",
        "--allow-reuse", "--no-metadata", "--no-art", "--no-preview",
        "--out", str(salida)])

    assert codigo == 0
    ini = next(salida.rglob("song.ini"))
    parser = configparser.ConfigParser()
    parser.read(ini, encoding="utf-8")
    # Las dos pistas estan de verdad en el chart, no solo en el nombre.
    texto = next(salida.rglob("notes.chart")).read_text(encoding="utf-8")
    assert "[ExpertSingle]" in texto and "[ExpertDoubleBass]" in texto
    assert parser["Song"]["diff_guitar"] == "3"
    assert parser["Song"]["diff_bass"] == "3"


# --------------------------------------------------------------------------
# Con audio real: lento
# --------------------------------------------------------------------------

@pytest.mark.slow
def test_with_real_audio_the_tempo_map_keeps_the_same_convention(real_midi, tmp_path,
                                                                 ffmpeg):
    """Minuto y medio de deteccion de beats. Fuera de la corrida normal.

    Lo que se comprueba es que el mapa ajustado al audio cumple la MISMA
    convencion que reproduce `tempo_from_midi`: el origen cae en un limite de
    compas y no antes del lead-in.
    """
    prepared = pipeline.prepare(
        real_midi, options=pipeline.Options(lookup_metadata=False,
                                            fetch_art=False, find_preview=False),
        log=lambda *a: None)

    assert prepared.audio is not None and prepared.audio.suffix == ".mp3"
    assert prepared.origin_tick % prepared.tempo_map.ticks_per_measure_at(0) == 0
    assert prepared.tempo_map.seconds_at_tick(prepared.origin_tick) >= PAD_SECONDS
    assert prepared.duration > 0
    assert prepared.tempo_note

    song, reports, _ = pipeline.build_song(prepared, {"guitar": ["voice"],
                                                      "drums": ["drums"]})
    assert song.track("guitar").notes and song.track("drums").notes
    assert len(reports) == 2
    # Ojo con lo que NO se puede exigir aqui: con el mapa ajustado al audio,
    # `origin_tick` es el primer DOWNBEAT detectado, no el final del lead-in, asi
    # que una anacrusa cae legitimamente antes. Lo que no puede haber es un tick
    # negativo, que seria musica fuera del archivo.
    assert all(n.tick >= 0 for t in song.tracks for n in t.notes)
    assert any(n.tick >= prepared.origin_tick
               for t in song.tracks for n in t.notes)
