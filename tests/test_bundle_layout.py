"""Lo primero que hay que asegurar: que se prueba el bundle y no el padre.

`ChartScript/` es una copia autocontenida y la suite se lanza desde el
repositorio padre, que tiene su propio `chartgen/` en el directorio de trabajo.
Si `import chartgen` resolviera al del padre, todos los demas tests pasarian
sin decir nada del bundle.
"""

from __future__ import annotations

from pathlib import Path

import chartgen
import hacer_chart
import pipeline
import tui
import variants
from conftest import BUNDLE


def test_chartgen_comes_from_the_bundle():
    assert Path(chartgen.__file__).parent.parent == BUNDLE


def test_the_loose_modules_come_from_the_bundle():
    for module in (pipeline, tui, variants, hacer_chart):
        assert Path(module.__file__).parent == BUNDLE


def test_the_symbols_the_bundle_advertises_exist():
    """Los nombres que leen `hacer_chart` y `tui` de `pipeline` y `variants`."""
    for name in ("find_audio", "tempo_from_midi", "tempo_from_audio",
                 "drums_track", "_snap_hits", "_drums_subdivision", "prepare",
                 "build_song", "build_variant", "Options", "Prepared",
                 "PipelineError", "CHANNELS", "MELODIC", "DRUMS",
                 "MAX_SIMULTANEOUS", "DRUMS_DROP_BUDGET", "AUDIO_EXTENSIONS",
                 "density_lines"):
        assert hasattr(pipeline, name), name
    for name in ("Variant", "Expansion", "expand", "single",
                 "varying_channels", "EMPTY", "SHORT", "MAX_VARIANTS"):
        assert hasattr(variants, name), name
    for name in ("resolve_track", "parse_assign", "parse_candidates",
                 "options_from", "build_parser", "main"):
        assert hasattr(hacer_chart, name), name
    for name in ("ask_indices", "Aborted", "say"):
        assert hasattr(tui, name), name


def test_drums_is_the_only_channel_outside_the_melodic_ones():
    assert pipeline.CHANNELS == pipeline.MELODIC + (pipeline.DRUMS,)
    assert pipeline.DRUMS not in pipeline.MELODIC
