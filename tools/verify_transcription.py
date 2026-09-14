"""Mide un chart transcrito contra uno de referencia.

La ventaja de este proyecto es que la verdad absoluta ya existe: para las
canciones con tablatura, `chartgen align` produce un chart cuyas notas estan
encajadas sobre la grabacion y verificadas contra sus ataques. Comparar la
transcripcion contra ESE chart convierte "suena bien" en un numero.

Se comparan TIEMPOS DE ATAQUE, no carriles. El carril sale de un mapeo de
contorno que reancla por frases, asi que dos fuentes distintas de la misma
musica no tienen por que coincidir en el boton sin que ninguna este mal. Lo que
si tiene que coincidir es cuando suena cada nota, que es lo que se juega.

Uso:
    python tools/verify_transcription.py out/transcrito out/referencia
    python tools/verify_transcription.py out/transcrito out/referencia --instrument guitar
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verify_alignment import chart_note_times  # noqa: E402

SECTION = {
    "bass": ("ExpertDoubleBass",),
    "guitar": ("ExpertSingle",),
    "rhythm": ("ExpertDoubleRhythm",),
    "keys": ("ExpertKeyboard",),
    "drums": ("ExpertDrums",),
}

DEFAULT_TOLERANCE = 0.05
"""Segundos. Es la ventana de acierto habitual en transcripcion automatica
(MIREX usa 50 ms) y ademas esta por debajo de lo que se nota al jugar."""


def match(found: list[float], truth: list[float],
          tolerance: float) -> tuple[list[tuple[float, float]], list[float], list[float]]:
    """Empareja cada nota detectada con una real, una a una.

    Se avanza por las dos listas ordenadas en vez de buscar el mas cercano para
    cada una: asi ninguna nota real puede justificar dos detectadas, que es como
    un detector que dispara el doble sacaria una nota perfecta.
    """
    pairs: list[tuple[float, float]] = []
    extra: list[float] = []
    missed: list[float] = []
    i = j = 0
    while i < len(found) and j < len(truth):
        delta = found[i] - truth[j]
        if abs(delta) <= tolerance:
            pairs.append((found[i], truth[j]))
            i += 1
            j += 1
        elif delta < 0:
            extra.append(found[i])
            i += 1
        else:
            missed.append(truth[j])
            j += 1
    extra.extend(found[i:])
    missed.extend(truth[j:])
    return pairs, extra, missed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidate", type=Path, help="Carpeta del chart transcrito.")
    ap.add_argument("reference", type=Path, help="Carpeta del chart de referencia.")
    ap.add_argument("--instrument", default="bass", choices=sorted(SECTION))
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = ap.parse_args()

    sections = SECTION[args.instrument]
    found = sorted(chart_note_times(args.candidate / "notes.chart", sections))
    truth = sorted(chart_note_times(args.reference / "notes.chart", sections))

    pairs, extra, missed = match(found, truth, args.tolerance)
    precision = len(pairs) / len(found) if found else 0.0
    recall = len(pairs) / len(truth) if truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)

    print(f"instrumento      : {args.instrument}")
    print(f"tolerancia       : {args.tolerance * 1000:.0f} ms")
    print(f"notas transcritas: {len(found)}")
    print(f"notas de refencia: {len(truth)}")
    print()
    print(f"aciertos         : {len(pairs)}")
    print(f"inventadas       : {len(extra)}")
    print(f"perdidas         : {len(missed)}")
    print()
    print(f"precision        : {precision:.1%}")
    print(f"recall           : {recall:.1%}")
    print(f"F1               : {f1:.1%}")

    if pairs:
        errors = [(a - b) * 1000.0 for a, b in pairs]
        print()
        print(f"sesgo temporal   : {statistics.median(errors):+.1f} ms "
              f"(mediana de los aciertos)")
        print(f"dispersion       : {statistics.median(abs(e) for e in errors):.1f} ms")

    # Un sesgo sistematico es otro problema que un F1 bajo: significa que el
    # chart entero esta corrido y se arregla moviendo, no re-transcribiendo.
    if pairs and abs(statistics.median((a - b) * 1000.0 for a, b in pairs)) > 20:
        print()
        print("AVISO: hay un sesgo constante. Antes de tocar el detector, mira el")
        print("       lead-in y el mapa de tempo: el chart puede estar solo corrido.")

    return 0 if f1 >= 0.5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
