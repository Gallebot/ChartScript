"""Verificacion end-to-end: los onsets del audio vs los ticks del .chart.

Parsea el .chart desde cero (sin usar TempoMap) y detecta los clicks del audio
con ffmpeg. Si ambos coinciden, la cadena tick -> segundos -> archivo es correcta.

Uso:
    python tools/verify_timing.py "out/chartgen - Calibration 120bpm"

Presupuesto de error medido (no adivinado):
  - Contra WAV sin comprimir el generador de clicks mide exacto: 0.02 ms.
  - Sobre el .ogg final, Vorbis introduce un sesgo de ~-2 ms en el ataque y
    algun outlier aislado de hasta ~+9 ms (pre-echo del codec).
Por eso la tolerancia absoluta es amplia: los bugs que este script debe cazar
(lead-in ausente, BPM equivocado, desfase de un beat) valen 100 ms o mas. Lo que
de verdad discrimina es la DERIVA: un error de BPM crece a lo largo de la
cancion, un artefacto del codec no.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

DETECTOR_BIAS_MS = -2.2
TOLERANCE_MS = 15.0
DRIFT_TOLERANCE_MS = 2.0


def chart_note_seconds(chart: Path) -> list[float]:
    text = chart.read_text(encoding="utf-8")
    res = int(re.search(r"Resolution = (\d+)", text).group(1))
    tempos = [(int(t), int(b) / 1000.0)
              for t, b in re.findall(r"(\d+) = B (\d+)", text)]
    if len(tempos) != 1:
        raise SystemExit("Este verificador solo soporta tempo constante (por ahora).")
    bpm = tempos[0][1]
    body = text.split("[ExpertSingle]")[1].split("}")[0]
    ticks = sorted({int(m.group(1)) for m in re.finditer(r"(\d+) = N [0-7] ", body)})
    return [t * 60.0 / (bpm * res) for t in ticks]


def audio_onset_seconds(ogg: Path) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(ogg),
         "-af", "silencedetect=noise=-50dB:d=0.05", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", proc.stderr)]


def main(folder: str) -> int:
    path = Path(folder)
    notes = chart_note_seconds(path / "notes.chart")
    onsets = audio_onset_seconds(path / "song.ogg")
    n = min(len(notes), len(onsets))
    if n == 0:
        raise SystemExit("No hay notas u onsets que comparar.")

    deltas = [(onsets[i] - notes[i]) * 1000 - DETECTOR_BIAS_MS for i in range(n)]
    worst = max(abs(d) for d in deltas)
    half = n // 2
    drift = (sum(deltas[half:]) / (n - half)) - (sum(deltas[:half]) / half)

    print(f"notas={len(notes)} onsets={len(onsets)} comparados={n}")
    print(f"desfase max (sesgo corregido) = {worst:.2f} ms  [tolerancia {TOLERANCE_MS}]")
    print(f"deriva entre mitades          = {drift:+.2f} ms  [tolerancia {DRIFT_TOLERANCE_MS}]")

    ok = worst < TOLERANCE_MS and abs(drift) < DRIFT_TOLERANCE_MS
    print("VEREDICTO:", "OK" if ok else "DESALINEADO")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
