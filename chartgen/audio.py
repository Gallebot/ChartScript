"""Preparacion de audio via ffmpeg. Sin dependencias de Python."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import PAD_SECONDS


class AudioError(RuntimeError):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise AudioError(
            f"No se encontro '{tool}' en el PATH. Instala ffmpeg y añadelo al PATH."
        )
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        raise AudioError(
            f"Fallo el comando: {' '.join(args[:2])}\n{proc.stderr[-2000:]}"
        )
    return proc


def _codec_args(dst: Path, quality: int) -> list[str]:
    """Vorbis para la salida real; PCM cuando se pide .wav.

    Los tests miden onsets con precision de microsegundos y Vorbis desplaza el
    ataque unos milisegundos, asi que necesitan una salida sin perdida.
    """
    if dst.suffix.lower() == ".wav":
        return ["-c:a", "pcm_s16le"]
    return ["-c:a", "libvorbis", "-q:a", str(quality)]


def probe_duration(path: Path) -> float:
    """Duracion en segundos."""
    out = _run([
        _require("ffprobe"), "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]).stdout
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise AudioError(f"No se pudo leer la duracion de {path}") from exc


def decode(src: Path, dst: Path, sample_rate: int | None = None,
           mono: bool = False) -> Path:
    """Decodifica a PCM sin tocar nada mas. Ni ganancia, ni padding, ni recorte.

    Existe por un motivo concreto: **quien decodifica un mp3 decide donde empieza**.
    Un mp3 lleva un retardo de codificador de unas 1100 muestras, y cada
    decodificador lo compensa a su manera. Si dos partes del pipeline usan
    decodificadores distintos sobre el mismo archivo, sus lineas de tiempo salen
    corridas entre si.

    Medido sobre Rolling in the Deep: pasandole el mp3 directamente a Demucs, el
    stem salia 667 muestras mas largo que lo que lee librosa y las notas
    transcritas caian 71 ms tarde respecto al chart de referencia. Decodificando
    antes con ffmpeg, la diferencia de longitud baja a UNA muestra.
    """
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise AudioError(f"No existe el audio de entrada: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    args = [_require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src)]
    if sample_rate:
        args += ["-ar", str(sample_rate)]
    if mono:
        args += ["-ac", "1"]
    args += ["-vn", "-c:a", "pcm_s16le", str(dst)]
    _run(args)
    return dst


def detect_peak_db(path: Path) -> float:
    """Pico maximo en dBFS, medido con el filtro volumedetect."""
    proc = subprocess.run(
        [_require("ffmpeg"), "-hide_banner", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", proc.stderr)
    if not match:
        raise AudioError("volumedetect no reporto max_volume")
    return float(match.group(1))


def prepare(
    src: Path,
    dst: Path,
    pad_seconds: float = PAD_SECONDS,
    target_peak_db: float | None = -1.0,
    quality: int = 8,
) -> Path:
    """Normaliza, añade el silencio de lead-in y exporta a Vorbis.

    El padding va DESPUES de la ganancia para que la normalizacion mida solo la
    musica real. `target_peak_db=None` desactiva la normalizacion.
    """
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise AudioError(f"No existe el audio de entrada: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    filters = []
    if target_peak_db is not None:
        gain = target_peak_db - detect_peak_db(src)
        filters.append(f"volume={gain:.2f}dB")
    if pad_seconds > 0:
        filters.append(f"adelay={int(round(pad_seconds * 1000))}:all=1")

    args = [_require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    if filters:
        args += ["-af", ",".join(filters)]
    args += ["-vn", *_codec_args(dst, quality), str(dst)]
    _run(args)
    return dst


def make_silence(dst: Path, seconds: float, quality: int = 8) -> Path:
    """Genera una pista de silencio. Util para probar el pipeline sin audio real."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        _require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={seconds}",
        *_codec_args(dst, quality), str(dst),
    ])
    return dst


def make_click_track(
    dst: Path, seconds: float, bpm: float, start_seconds: float = PAD_SECONDS
) -> Path:
    """Metronomo sintetizado: un click por negra a partir de `start_seconds`.

    Es la referencia para verificar que el chart de calibracion cae exactamente
    sobre el beat: si las notas y los clicks no coinciden, el bug esta en la
    conversion tick<->segundos, no en la percepcion.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    period = 60.0 / bpm
    # Un seno de 1 kHz recortado a los primeros 20 ms de cada periodo.
    # La fase del seno se bloquea a cada click (se usa el resto, no t absoluto):
    # asi todos los clicks son la misma forma de onda y su onset es identico.
    # Con fase libre cada click ataca distinto y los detectores de onset lo miden
    # con varios ms de dispersion.
    phase = f"mod(t-{start_seconds},{period})"
    expr = (
        f"sin(2*PI*1000*{phase})*lt({phase},0.02)*gte(t,{start_seconds})"
    )
    # Las comas de mod()/lt() separarian opciones del filtro: hay que escaparlas.
    expr = expr.replace(",", r"\,")
    _run([
        _require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"aevalsrc=exprs={expr}:s=44100:d={seconds}",
        "-af", "volume=-6dB", *_codec_args(dst, 6), str(dst),
    ])
    return dst


def find_preview_start(path: Path, window: float = 30.0,
                       skip_intro: float = 20.0) -> float:
    """Instante mas energico de la cancion, para `preview_start_time`.

    Busca la ventana de `window` segundos con mayor energia RMS media. Es un
    proxy del estribillo, que es lo que un charter elegiria a mano. Se salta el
    principio porque las intros suelen ser tranquilas y porque, con el lead-in,
    los primeros segundos son silencio.
    """
    import librosa
    import numpy as np

    duration = probe_duration(path)
    if duration <= window:
        return 0.0

    y, sr = librosa.load(str(path), mono=True, sr=22050)
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    frames_per_window = max(1, int(window * sr / hop))
    if len(rms) <= frames_per_window:
        return 0.0

    # Media movil de la energia mediante suma acumulada.
    cumulative = np.concatenate([[0.0], np.cumsum(rms)])
    sums = cumulative[frames_per_window:] - cumulative[:-frames_per_window]

    first = min(int(skip_intro * sr / hop), max(0, len(sums) - 1))
    best = first + int(np.argmax(sums[first:])) if first < len(sums) else 0
    start = best * hop / sr

    # Nunca proponer un preview que se salga del final del archivo.
    return float(min(start, max(0.0, duration - window)))
