"""Bateria de tests del bundle ChartScript.

## Lo que hace este conftest

Pone `ChartScript/` al PRINCIPIO de `sys.path`. Dos razones, y la segunda importa
mas que la primera:

1. `import pipeline`, `import tui`, `import variants` y `import hacer_chart` son
   modulos sueltos en la raiz del bundle, no un paquete instalado.
2. `import chartgen` tiene que resolver a la COPIA que vive dentro del bundle. La
   suite se lanza desde el repositorio padre, que tiene su propio `chartgen/` en
   el directorio de trabajo: sin insertar delante, se estaria probando el paquete
   del proyecto grande y no el del bundle, que es justo lo que estos tests
   existen para verificar.

## Convenciones de los tests

- Nada depende de deteccion de beats. Los mapas de tempo salen de
  `TempoMap.constant(120)` o de `pipeline.tempo_from_midi`; el unico test que
  toca audio real lleva `@pytest.mark.slow` y esta fuera de la corrida normal.
- A 120 BPM el lead-in de `PAD_SECONDS` (2 s) son exactamente 4 negras = 768
  ticks, asi que una nota en el segundo `k * 0.5` del audio original cae en el
  tick `768 + k * 192` sin redondeos. Los tests de bateria se apoyan en eso para
  poder afirmar ticks exactos en vez de rangos.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parent.parent
if sys.path[:1] != [str(BUNDLE)]:
    sys.path.insert(0, str(BUNDLE))

import pipeline  # noqa: E402
from chartgen import smf  # noqa: E402
from chartgen.frontends import midi as midi_frontend  # noqa: E402
from chartgen.ir import Metadata  # noqa: E402
from chartgen.tempo import TempoMap  # noqa: E402
from chartgen.transcribe import TranscribedNote  # noqa: E402

EXAMPLES = BUNDLE.parent / "ejemplos-gp"
REAL_MIDI = EXAMPLES / "DANNA - SI SE ACABA EL MUNDO.mid"


# --------------------------------------------------------------------------
# MIDI sinteticos
# --------------------------------------------------------------------------

def build_midi(path: Path, division: int = 480,
               tempos=((0, 120.0),),
               signatures=((0, 4, 4),),
               notes=((0, 480, 60),),
               track_name: str = "electric_bass") -> Path:
    """Escribe un SMF de formato 1 con pista de tempo + una pista de notas.

    Los ticks van en la `division` que se pida, que es el punto de varios tests:
    el pipeline trabaja a 192 y tiene que reescalar lo que le entre.
    """
    conductor = [smf.text(0, smf.META_TRACK_NAME, "conductor")]
    conductor += [smf.tempo(tick, bpm) for tick, bpm in tempos]
    conductor += [smf.time_signature(tick, num, den)
                  for tick, num, den in signatures]

    part = [smf.text(0, smf.META_TRACK_NAME, track_name)]
    for start, end, pitch in notes:
        part.append(smf.note_on(start, pitch, 100, 0))
        part.append(smf.note_off(end, pitch, 0))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(smf.render([conductor, part], division))
    return path


@pytest.fixture
def make_midi(tmp_path):
    """Factoria de MIDI sinteticos dentro de `tmp_path`."""
    counter = {"n": 0}

    def factory(name: str | None = None, **kwargs) -> Path:
        counter["n"] += 1
        name = name or f"sintetico{counter['n']}.mid"
        return build_midi(tmp_path / name, **kwargs)

    return factory


# --------------------------------------------------------------------------
# Notas y Prepared sin pasar por disco
# --------------------------------------------------------------------------

def note(start: float, pitch: int, duration: float = 0.2) -> TranscribedNote:
    """Una nota transcrita en segundos del audio ORIGINAL (sin lead-in)."""
    return TranscribedNote(start=start, end=start + duration,
                           pitch=float(pitch), confidence=1.0)


def source_track(name: str, instrument: str | None,
                 notes: list[TranscribedNote]) -> midi_frontend.SourceTrack:
    return midi_frontend.SourceTrack(name=name, instrument=instrument,
                                     notes=notes)


@pytest.fixture
def make_prepared(tmp_path):
    """`Prepared` armado a mano: sin audio, sin metadatos y con tempo constante.

    `prepare()` completo llama a MusicBrainz y a la deteccion de beats; para
    probar `build_song` no hace falta ninguna de las dos, y meterlas haria la
    suite lenta y dependiente de la red.
    """
    def factory(tracks, tempo_map: TempoMap | None = None,
                origin_tick: int = 0, options: pipeline.Options | None = None,
                duration: float = 120.0) -> pipeline.Prepared:
        return pipeline.Prepared(
            midi=tmp_path / "inexistente.mid",
            audio=None,
            tracks=list(tracks),
            tempo_map=tempo_map or TempoMap.constant(120),
            origin_tick=origin_tick,
            tempo_note="mapa de prueba",
            tempo_reliable=True,
            metadata=Metadata(name="Titulo", artist="Artista"),
            art=None,
            preview_start=2.0,
            duration=duration,
            options=options or pipeline.Options(cleanup=False, star_power=False,
                                                subdivision=4,
                                                lookup_metadata=False,
                                                fetch_art=False,
                                                find_preview=False),
        )

    return factory


# --------------------------------------------------------------------------
# El MIDI real
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def real_midi() -> Path:
    if not REAL_MIDI.exists():
        pytest.skip(f"falta el MIDI de ejemplo: {REAL_MIDI}")
    return REAL_MIDI


@pytest.fixture
def lonely_midi(tmp_path, real_midi) -> Path:
    """El MIDI real copiado SIN su mp3 al lado.

    Asi `find_audio` no encuentra nada y el pipeline toma el tempo del propio
    MIDI, que es lo rapido. Con el mp3 al lado cada test costaria minuto y medio.
    """
    destination = tmp_path / real_midi.name
    shutil.copy(real_midi, destination)
    return destination


@pytest.fixture
def ffmpeg() -> None:
    """Salta el test si ffmpeg/ffprobe no estan en el PATH."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            pytest.skip(f"'{tool}' no esta en el PATH")
