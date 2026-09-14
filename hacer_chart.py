"""Punto de entrada de ChartScript: MIDI -> carpeta(s) lista(s) para Clone Hero.

Se puede usar de dos formas:

**Interactiva** (lo que hace el .bat al arrastrarle un MIDI). Muestra las pistas
del archivo y pregunta que va a que canal:

    python hacer_chart.py "Artista - Titulo.mid"

**No interactiva**, para repetir algo ya decidido o para scripts:

    python hacer_chart.py "x.mid" --assign "voice=guitar,electric_bass=bass"
    python hacer_chart.py "x.mid" --candidates "guitar=voice|electric_bass,drums=drums"

`--assign` genera una carpeta. `--candidates` genera una por combinacion: las
pistas candidatas de un canal se separan con `|`, los canales con comas.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pipeline  # noqa: E402
import tui  # noqa: E402
import variants as variants_mod  # noqa: E402
from chartgen import audio as audio_mod  # noqa: E402
from chartgen import beats as beats_mod  # noqa: E402
from chartgen import lyrics as lyrics_mod  # noqa: E402
from chartgen import package as package_mod  # noqa: E402
from chartgen import tempo_fit  # noqa: E402
from chartgen.frontends import midi as midi_frontend  # noqa: E402
from chartgen.frontends import transcription as tx_frontend  # noqa: E402

DEFAULT_OUT = HERE / "out"

say = tui.say


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hacer_chart",
        description="MIDI ya transcrito -> carpeta de Clone Hero, editable en "
                    "Moonscraper.")
    p.add_argument("midi", type=Path, nargs="+",
                   help="Uno o varios MIDI. Si al lado de cada uno hay un audio "
                        "con el mismo nombre, se usa para ajustar el tempo.")
    p.add_argument("--audio", type=Path, default=None,
                   help="Audio explicito. Solo valido con un MIDI.")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT,
                   help=f"Carpeta de salida. Por defecto {DEFAULT_OUT}")
    p.add_argument("--format", default="chart", choices=["chart", "mid", "both"],
                   help="notes.chart (por defecto: Clone Hero lo juega y "
                        "Moonscraper lo edita), notes.mid, o los dos. Con los "
                        "dos, Clone Hero se queda con el .mid e ignora el .chart.")
    p.add_argument("--force", action="store_true",
                   help="Pisa una carpeta que ya exista con ese nombre.")

    g = p.add_argument_group("asignacion no interactiva")
    g.add_argument("--assign", default=None,
                   help="'pista=canal,pista=canal'. Una sola carpeta. Varias "
                        "pistas al mismo canal se suman.")
    g.add_argument("--candidates", default=None,
                   help="'canal=pistaA|pistaB,canal=pistaC'. Una carpeta por "
                        "combinacion. Un 0 como candidato agrega la opcion de "
                        "dejar el canal vacio.")
    g.add_argument("--allow-reuse", action="store_true",
                   help="Deja que la misma pista caiga en dos canales.")
    g.add_argument("--max-variants", type=int, default=variants_mod.MAX_VARIANTS,
                   help=f"Tope de carpetas. Por defecto {variants_mod.MAX_VARIANTS}.")
    g.add_argument("--list", action="store_true",
                   help="Solo enumera las pistas del MIDI y sale.")

    t = p.add_argument_group("tempo y cuantizacion")
    t.add_argument("--detector", default="auto",
                   choices=["auto", "librosa", "beat_this"])
    t.add_argument("--bpm-hint", type=float, default=None)
    t.add_argument("--max-error", type=float, default=tempo_fit.DEFAULT_MAX_ERROR)
    t.add_argument("--subdivision", type=int, default=None,
                   choices=list(tx_frontend.SUBDIVISIONS))
    t.add_argument("--tolerance", type=float, default=tx_frontend.DEFAULT_TOLERANCE)
    t.add_argument("--no-cleanup", action="store_true")
    t.add_argument("--no-star-power", action="store_true")

    e = p.add_argument_group("extras")
    e.add_argument("--sections", action="store_true",
                   help="Detecta secciones del audio. Necesita audio.")
    e.add_argument("--lyrics", type=Path, default=None, help="Archivo .lrc.")
    e.add_argument("--lyrics-mode", default="line",
                   choices=["line", "word", "syllable"])
    e.add_argument("--lang", default=None)
    e.add_argument("--title", default=None)
    e.add_argument("--artist", default=None)
    e.add_argument("--album", default=None)
    e.add_argument("--genre", default=None)
    e.add_argument("--year", default=None)
    e.add_argument("--charter", default="chartgen/midi")
    e.add_argument("--no-metadata", action="store_true")
    e.add_argument("--no-art", action="store_true")
    e.add_argument("--no-preview", action="store_true")
    e.add_argument("--art", type=Path, default=None)
    return p


def options_from(args) -> pipeline.Options:
    return pipeline.Options(
        detector=args.detector,
        bpm_hint=args.bpm_hint,
        max_error=args.max_error,
        subdivision=args.subdivision,
        tolerance=args.tolerance,
        cleanup=not args.no_cleanup,
        star_power=not args.no_star_power,
        formats=("chart", "mid") if args.format == "both" else (args.format,),
        detect_sections=args.sections,
        lyrics=args.lyrics,
        lyrics_mode=args.lyrics_mode,
        lang=args.lang,
        title=args.title,
        artist=args.artist,
        album=args.album,
        genre=args.genre,
        year=args.year,
        charter=args.charter,
        lookup_metadata=not args.no_metadata,
        fetch_art=not args.no_art,
        art=args.art,
        find_preview=not args.no_preview,
        overwrite=args.force,
    )


def resolve_track(name: str, tracks) -> str:
    """Acepta el nombre de la pista, su indice (1-based) o el instrumento deducido."""
    name = name.strip()
    if name.isdigit():
        index = int(name)
        if not 1 <= index <= len(tracks):
            raise ValueError(f"indice de pista fuera de rango: {index} "
                             f"(hay {len(tracks)})")
        return tracks[index - 1].name
    lowered = name.lower()
    for track in tracks:
        if track.name.lower() == lowered:
            return track.name
    for track in tracks:
        if track.instrument and track.instrument.lower() == lowered:
            return track.name
    known = ", ".join(t.name for t in tracks)
    raise ValueError(f"el MIDI no tiene la pista '{name}'. Tiene: {known}")


def parse_assign(raw: str, tracks) -> dict[str, str]:
    """'voice=guitar,electric_bass=bass' -> {'voice': 'guitar', ...}."""
    assignment: dict[str, str] = {}
    for pair in raw.split(","):
        if not pair.strip():
            continue
        left, sep, right = pair.partition("=")
        if not sep:
            raise ValueError(f"--assign: falta el '=' en '{pair.strip()}'")
        channel = right.strip().lower()
        if channel not in pipeline.CHANNELS:
            raise ValueError(f"--assign: canal desconocido '{channel}'. "
                             f"Hay: {', '.join(pipeline.CHANNELS)}")
        assignment[resolve_track(left, tracks)] = channel
    if not assignment:
        raise ValueError("--assign esta vacio")
    return assignment


def parse_candidates(raw: str, tracks) -> dict[str, list[str | None]]:
    """'guitar=voice|bass,drums=drums' -> {'guitar': ['voice','bass'], ...}."""
    choices: dict[str, list[str | None]] = {}
    for group in raw.split(","):
        if not group.strip():
            continue
        left, sep, right = group.partition("=")
        if not sep:
            raise ValueError(f"--candidates: falta el '=' en '{group.strip()}'")
        channel = left.strip().lower()
        if channel not in pipeline.CHANNELS:
            raise ValueError(f"--candidates: canal desconocido '{channel}'. "
                             f"Hay: {', '.join(pipeline.CHANNELS)}")
        candidates: list[str | None] = []
        for item in right.split("|"):
            item = item.strip()
            if not item:
                continue
            value = (variants_mod.EMPTY if item == "0"
                     else resolve_track(item, tracks))
            if value not in candidates:
                candidates.append(value)
        if candidates:
            choices[channel] = candidates
    if not choices:
        raise ValueError("--candidates esta vacio")
    return choices


def run_one(midi: Path, args) -> int:
    options = options_from(args)
    out_root = Path(args.out)

    if args.list:
        for line in midi_frontend.describe(midi):
            say(line)
        return 0

    if not (args.assign or args.candidates):
        return tui.run(midi, out_root, options)

    # --- Camino no interactivo -------------------------------------------
    tracks = midi_frontend.read_tracks(midi)
    if args.assign:
        items = [variants_mod.single(parse_assign(args.assign, tracks))]
        varying: tuple[str, ...] = ()
    else:
        choices = parse_candidates(args.candidates, tracks)
        expansion = variants_mod.expand(choices, allow_reuse=args.allow_reuse)
        for note in expansion.notes():
            say(f"AVISO: {note}")
        items = expansion.variants
        if not items:
            raise ValueError(
                "la combinatoria no dejo ninguna variante valida; suele pasar "
                "por repetir la misma pista en dos canales (usa --allow-reuse)")
        if len(items) > args.max_variants:
            say(f"AVISO: saldrian {len(items)} carpetas; se generan las "
                f"{args.max_variants} primeras (--max-variants lo cambia).")
            items = items[:args.max_variants]
        varying = variants_mod.varying_channels(choices)

    audio = Path(args.audio) if args.audio else None
    prepared = pipeline.prepare(midi, audio, options, log=say)
    say(f"  tempo: {prepared.tempo_note}")
    if not prepared.tempo_reliable:
        say("  AVISO: esta grabacion no se deja modelar bien. La cuantizacion")
        say("         hereda ese error, asi que revisa el resultado.")

    built = 0
    for index, variant in enumerate(items, 1):
        suffix = variant.label(varying) if len(items) > 1 else ""
        say()
        say(f"[{index}/{len(items)}] {variant.describe()}")
        try:
            result = pipeline.build_variant(prepared, variant.assignment,
                                            out_root, title_suffix=suffix, log=say)
        except (pipeline.PipelineError, package_mod.OverwriteError,
                ValueError, OSError) as exc:
            say(f"  ERROR: {exc}")
            continue
        for channel, report in result.reports.items():
            say(f"  {channel:<7} {report}")
        for warning in result.warnings:
            say(f"  AVISO: {warning}")
        say(f"  -> {result.folder}")
        built += 1

    if prepared.art is not None and prepared.art.name == "_cover.jpg":
        prepared.art.unlink(missing_ok=True)
    return 0 if built else 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.audio and len(args.midi) > 1:
        say("ERROR: --audio solo vale con un MIDI. Con varios, pon cada audio "
            "al lado de su MIDI con el mismo nombre.")
        return 1

    Path(args.out).mkdir(parents=True, exist_ok=True)

    worst = 0
    for index, midi in enumerate(args.midi, 1):
        if len(args.midi) > 1:
            say()
            say(f"########## [{index}/{len(args.midi)}] {midi.name} ##########")
        try:
            worst = max(worst, run_one(Path(midi), args))
        except tui.Aborted as exc:
            say(f"Cancelado: {exc}")
            worst = max(worst, 1)
        except (pipeline.PipelineError, midi_frontend.MidiFrontendError,
                tx_frontend.TranscriptionFrontendError, lyrics_mod.LyricsError,
                beats_mod.BeatDetectionError, audio_mod.AudioError,
                package_mod.OverwriteError,
                ValueError, FileNotFoundError, OSError) as exc:
            say(f"ERROR: {exc}")
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
