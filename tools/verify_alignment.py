"""Comprueba objetivamente si un chart encaja con su grabacion.

Reparsea el .chart desde cero (sin usar el codigo del pipeline) y compara los
tiempos de nota con los ataques reales del audio en la banda del instrumento.
Prueba distintos desplazamientos y dice cual seria el optimo: si sale ~0, el
chart esta alineado.

Uso:
    python tools/verify_alignment.py "out/Artista - Titulo" ruta/original.mp3
    python tools/verify_alignment.py "out/Artista - Titulo" original.mp3 --guitar

El audio debe ser el ORIGINAL, no el song.ogg de la carpeta: ese lleva ya el
lead-in de 2 s y falsearia la medida.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chartgen import PAD_SECONDS  # noqa: E402
from chartgen.align import INSTRUMENT_BANDS, onset_envelope  # noqa: E402

SECTIONS = ("ExpertDoubleBass", "ExpertSingle", "ExpertDoubleRhythm",
            "ExpertDoubleGuitar", "ExpertKeyboard")


def chart_note_times(chart: Path, sections=SECTIONS,
                     pad: float = PAD_SECONDS) -> list[float]:
    """Tiempos de nota en la linea de tiempo del audio ORIGINAL (sin lead-in).

    `pad=0` para charts ajenos: los de una biblioteca de Clone Hero no llevan el
    lead-in que añade este pipeline, su propio audio ES la linea de tiempo.
    """
    text = chart.read_text(encoding="utf-8")
    resolution = int(re.search(r"Resolution = (\d+)", text).group(1))
    tempos = sorted((int(t), int(b) / 1000.0)
                    for t, b in re.findall(r"(\d+) = B (\d+)", text))
    if not tempos:
        raise SystemExit("El chart no tiene eventos de tempo.")

    def seconds(tick: int) -> float:
        total, prev_tick, prev_bpm = 0.0, 0, tempos[0][1]
        for at, bpm in tempos:
            if at >= tick:
                break
            total += (at - prev_tick) * 60.0 / (prev_bpm * resolution)
            prev_tick, prev_bpm = at, bpm
        return total + (tick - prev_tick) * 60.0 / (prev_bpm * resolution)

    for name in sections:
        if f"[{name}]" in text:
            body = text.split(f"[{name}]")[1].split("}")[0]
            ticks = sorted({int(m.group(1))
                            for m in re.finditer(r"(\d+) = N [0-4] ", body)})
            if ticks:
                return [seconds(t) - pad for t in ticks]
    raise SystemExit("No se encontraron notas en el chart.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", type=Path)
    ap.add_argument("audio", type=Path, help="El mp3/wav ORIGINAL, no song.ogg")
    ap.add_argument("--instrument", default="bass", choices=sorted(INSTRUMENT_BANDS))
    ap.add_argument("--range", type=float, default=1.5,
                    help="Segundos de busqueda a cada lado.")
    args = ap.parse_args()

    import numpy as np

    notes = np.array(chart_note_times(args.folder / "notes.chart"))
    env, times = onset_envelope(args.audio, INSTRUMENT_BANDS[args.instrument])

    def score(shift: float) -> float:
        index = np.searchsorted(times, notes + shift)
        index = index[(index > 0) & (index < len(env))]
        return float(env[index].mean()) if len(index) else -1.0

    shifts = np.arange(-args.range, args.range + 0.001, 0.01)
    values = np.array([score(s) for s in shifts])
    best = float(shifts[int(values.argmax())])

    bpm = 60.0 / np.median(np.diff(notes)) if len(notes) > 2 else 0.0
    print(f"notas analizadas : {len(notes)}")
    print(f"score actual     : {score(0.0):.3f}")
    print(f"score optimo     : {values.max():.3f} con {best:+.3f}s")
    if bpm:
        print(f"                   ({best / (60.0 / bpm):+.2f} pulsos aprox.)")

    # Dos condiciones, y hace falta cumplir las dos. El desplazamiento pequeño
    # solo dice que no hay nada mejor CERCA; la fraccion dice si las notas caen
    # de verdad sobre los ataques. En Rolling in the Deep el optimo estaba a
    # 30 ms y aun asi el score era el 10% del alcanzable: la curva era plana
    # porque el bajo no destaca en la mezcla, y dar eso por bueno era engañoso.
    ratio = score(0.0) / values.max() if values.max() > 0 else 0.0
    centred = abs(best) < 0.05
    strong = ratio > 0.6
    print(f"fraccion alcanzada: {ratio:.0%}   [se pide >60%]")

    if centred and strong:
        print("VEREDICTO: ALINEADO")
        return 0
    if centred and not strong:
        print("VEREDICTO: SIN SEÑAL")
        print("  Las notas no caen sobre ataques claros en la banda del")
        print("  instrumento. Puede que no destaque en la mezcla, o que la")
        print("  tablatura no siga la grabacion. Revisalo a mano.")
        return 2
    print("VEREDICTO: CORRIDO")
    print("  Reajusta con --offset-beats en chartgen align.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
