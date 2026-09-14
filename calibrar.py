"""Chart de calibracion: una nota por negra sobre un metronomo.

## Para que sirve tener esto dentro del bundle

Es la unica forma de saber si la cadena `tick -> segundos -> archivo` sigue bien
sin depender del oido. El chart pone una nota en cada negra y el audio un click
en cada negra: si al jugarlo las notas no caen sobre los clicks, el problema esta
en el pipeline y no en el MIDI de entrada.

Es lo primero que hay que correr cuando algo "suena corrido" y no se sabe si la
culpa es del MIDI, del detector de beats o del empaquetado. Aqui no hay MIDI ni
detector: si esto falla, falla la base.

## Como se usa

    python calibrar.py                 # 120 BPM, 30 s, con metronomo
    python calibrar.py --bpm 174.267   # un BPM no redondo, que es lo que rompe
    python calibrar.py --silence       # sin audio, solo para probar el escaneo

Y despues, para medirlo en vez de jugarlo:

    python tools/verify_timing.py "out/chartgen - Calibration 120bpm"
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from chartgen import PAD_SECONDS  # noqa: E402
from chartgen import audio as audio_mod  # noqa: E402
from chartgen import calibration, package  # noqa: E402

DEFAULT_OUT = HERE / "out"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="calibrar",
        description="Genera un chart de calibracion para verificar el timing.")
    p.add_argument("--bpm", type=float, default=120.0,
                   help="Prueba tambien un BPM no redondo: 174.267 es el que "
                        "caza errores de cuantizacion del evento B.")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--format", default="chart", choices=["chart", "mid", "both"])
    p.add_argument("--silence", action="store_true",
                   help="Sin metronomo. Solo prueba que el juego escanea la "
                        "carpeta; no sirve para verificar timing.")
    p.add_argument("--audio", type=Path, default=None,
                   help="Usa este audio en vez de generar el metronomo.")
    p.add_argument("--verify", action="store_true",
                   help="Mide el resultado al terminar, en vez de dejarlo para "
                        "que lo juegues. Necesita ffmpeg y el formato .chart.")
    args = p.parse_args(argv)

    if args.verify and args.silence:
        p.error("--verify no tiene sentido con --silence: un audio en silencio "
                "no tiene clicks que comparar.")
    if args.verify and args.format == "mid":
        p.error("--verify lee notes.chart; usa --format chart o both.")

    formats = ("chart", "mid") if args.format == "both" else (args.format,)
    song = calibration.build(bpm=args.bpm, seconds=args.seconds)

    if args.audio:
        result = package.build(song, args.out, audio_src=Path(args.audio),
                               pad_seconds=PAD_SECONDS, formats=formats,
                               overwrite=True)
    elif args.silence:
        result = package.build(song, args.out, formats=formats,
                               overwrite=True)
    else:
        # El click se ancla a la PRIMERA NOTA REAL del chart, no al lead-in
        # nominal: a BPM no redondos no son el mismo instante, y anclarlo al
        # lead-in metia un desfase que parecia un bug del pipeline.
        first_tick = song.tracks[0].sorted_notes()[0].tick
        first_seconds = song.tempo_map.seconds_at_tick(first_tick)
        with tempfile.TemporaryDirectory() as tmp:
            click = audio_mod.make_click_track(
                Path(tmp) / "click.ogg", first_seconds + args.seconds + 2.0,
                args.bpm, start_seconds=first_seconds)
            result = package.build(song, args.out, audio_src=click,
                                   pad_seconds=0.0, normalize=False,
                                   formats=formats, overwrite=True)

    print(result.describe())
    print()
    print(f"Notas: {len(song.tracks[0].notes)} | BPM {args.bpm:g} | "
          f"lead-in {PAD_SECONDS:g}s")
    if args.verify:
        print()
        sys.path.insert(0, str(HERE / "tools"))
        import verify_timing
        return verify_timing.main(str(result.folder))

    if not args.silence:
        print("Las notas deben caer exactamente sobre cada click del metronomo.")
        print()
        print("Para medirlo en vez de jugarlo:")
        print(f'  python tools/verify_timing.py "{result.folder}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
