"""Deteccion de beats y downbeats.

Frontend intercambiable: la logica que convierte beats en `[SyncTrack]` vive en
`chartgen.tempo_fit` y no sabe que detector la alimenta. `LibrosaBeats` es la
implementacion barata para desarrollar; `BeatThisBeats` es la buena, y corre en
su propio venv por subprocess para no meter torch en el core.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class BeatDetectionError(RuntimeError):
    pass


def _dedupe(times, min_gap: float = 1e-4) -> list[float]:
    out: list[float] = []
    for t in sorted(float(x) for x in times):
        if not out or t - out[-1] >= min_gap:
            out.append(t)
    return out


@dataclass
class Beats:
    """Resultado de un detector. Tiempos en segundos sobre el audio analizado."""

    beat_times: list[float]
    downbeat_times: list[float] = field(default_factory=list)
    source: str = "unknown"
    strengths: list[float] = field(default_factory=list)
    """Energia de onset en cada beat. Opcional; la usa el alineado para estimar
    en que pulso de la grabacion empieza la tablatura."""

    def __post_init__(self) -> None:
        # Se deduplican: dos beats en el mismo instante dividirian por cero al
        # calcular el BPM del tramo.
        self.beat_times = _dedupe(self.beat_times)
        self.downbeat_times = _dedupe(self.downbeat_times)
        if len(self.beat_times) < 2:
            raise BeatDetectionError(
                f"{self.source}: se detectaron {len(self.beat_times)} beats; "
                "insuficiente para construir un mapa de tempo."
            )

    @property
    def median_bpm(self) -> float:
        gaps = sorted(b - a for a, b in zip(self.beat_times, self.beat_times[1:]))
        return 60.0 / gaps[len(gaps) // 2]

    def beats_per_measure(self, default: int = 4) -> int:
        """Numero de beats entre downbeats consecutivos (el mas frecuente)."""
        if len(self.downbeat_times) < 2:
            return default
        counts: dict[int, int] = {}
        for a, b in zip(self.downbeat_times, self.downbeat_times[1:]):
            n = sum(1 for t in self.beat_times if a - 1e-6 <= t < b - 1e-6)
            if n > 0:
                counts[n] = counts.get(n, 0) + 1
        if not counts:
            return default
        return max(counts, key=lambda k: counts[k])

    def shifted(self, seconds: float) -> Beats:
        return Beats(
            [t + seconds for t in self.beat_times],
            [t + seconds for t in self.downbeat_times],
            self.source,
            list(self.strengths),
        )

    def to_json(self) -> str:
        return json.dumps({
            "beat_times": self.beat_times,
            "downbeat_times": self.downbeat_times,
            "source": self.source,
        })

    @classmethod
    def from_json(cls, text: str) -> Beats:
        d = json.loads(text)
        return cls(d["beat_times"], d.get("downbeat_times", []),
                   d.get("source", "json"))


class BeatDetector(Protocol):
    name: str

    def detect(self, audio: Path) -> Beats: ...


class LibrosaBeats:
    """Beat tracking con librosa. Sin torch, se instala en segundos.

    Usa PLP (predominant local pulse) en lugar de `beat_track`, que asume un
    tempo global: en una pista que pasa de 128 a 96 BPM, `beat_track` aplana
    todo a 96 y se come la primera seccion entera. PLP sigue el pulso local.

    Encima aplica dos refinamientos, ambos medidos sobre percusion sintetica de
    tempo conocido:
      - *snap*: cada beat se encaja al onset real mas cercano. Los maximos de
        PLP son gruesos; encajarlos bajo el error medio de 33 ms a 5.5 ms.
      - *prune*: descarta beats con energia de onset despreciable, que es de
        donde salen las detecciones espurias en colas y silencios.

    librosa NO detecta downbeats: se infieren probando cada fase del compas y
    eligiendo aquella cuyos beats caen sobre los onsets mas fuertes. Funciona
    con acentuacion clara y falla con sincopa densa; para eso esta BeatThisBeats.
    """

    name = "librosa"

    def __init__(
        self,
        meter: int = 4,
        method: str = "plp",
        sr: int = 44100,
        hop_length: int = 256,
        snap: bool = True,
        prune: bool = True,
        bpm_hint: float | None = None,
        hint_window: tuple[float, float] = (0.8, 1.25),
    ) -> None:
        self.meter = meter
        self.method = method
        self.sr = sr
        self.hop_length = hop_length
        self.snap = snap
        self.prune = prune
        self.bpm_hint = bpm_hint
        self.hint_window = hint_window

    def tempo_range(self) -> tuple[float, float] | None:
        """Rango de tempo permitido, si hay pista de por donde va.

        Sin restringir, PLP se engancha a subdivisiones: en 24K Magic (107 BPM
        reales) detectaba 760 beats a 215 BPM, justo el doble, y el encaje con la
        tablatura era imposible. Con el rango puesto alrededor del tempo del tab
        salen 405 beats a 107.7 BPM.
        """
        if not self.bpm_hint:
            return None
        low, high = self.hint_window
        return self.bpm_hint * low, self.bpm_hint * high

    def detect(self, audio: Path) -> Beats:
        import librosa
        import numpy as np

        y, sr = librosa.load(str(audio), mono=True, sr=self.sr)
        env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=self.hop_length)

        limits = self.tempo_range()
        if self.method == "plp":
            extra = {}
            if limits:
                extra["tempo_min"], extra["tempo_max"] = limits
            pulse = librosa.beat.plp(onset_envelope=env, sr=sr,
                                     hop_length=self.hop_length, **extra)
            frames = np.flatnonzero(librosa.util.localmax(pulse))
        elif self.method == "beat_track":
            _, frames = librosa.beat.beat_track(
                onset_envelope=env, sr=sr, hop_length=self.hop_length, trim=False,
                start_bpm=self.bpm_hint or 120.0,
            )
            frames = np.asarray(frames)
        else:
            raise BeatDetectionError(f"Metodo desconocido: {self.method}")

        if len(frames) < 2:
            raise BeatDetectionError("librosa no encontro beats suficientes.")

        if self.prune:
            frames = self._prune(frames, env)
        if self.snap:
            frames = self._snap(frames, env, sr)

        beat_times = librosa.frames_to_time(frames, sr=sr,
                                            hop_length=self.hop_length)
        downbeats = self._infer_downbeats(frames, env, beat_times)
        strengths = env[np.clip(frames, 0, len(env) - 1)]
        return Beats(list(map(float, beat_times)), list(map(float, downbeats)),
                     source=f"librosa/{self.method}",
                     strengths=list(map(float, strengths)))

    def _prune(self, frames, env, floor: float = 0.25):
        import numpy as np

        strengths = env[np.clip(frames, 0, len(env) - 1)]
        keep = frames[strengths >= floor * np.median(strengths)]
        return keep if len(keep) >= 2 else frames

    def _snap(self, frames, env, sr):
        """Encaja cada beat al onset detectado mas fuerte de su vecindad."""
        import librosa
        import numpy as np

        onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr,
                                            hop_length=self.hop_length)
        if len(onsets) == 0 or len(frames) < 2:
            return frames
        # Ventana de un cuarto de beat: mas ancha y se robaria el beat vecino.
        window = max(1, int(np.median(np.diff(frames)) / 4))
        snapped = []
        for f in frames:
            near = onsets[np.abs(onsets - f) <= window]
            snapped.append(int(near[np.argmax(env[near])]) if len(near) else int(f))
        return np.array(sorted(set(snapped)))

    def _infer_downbeats(self, frames, env, beat_times):
        import numpy as np

        strengths = env[np.clip(frames, 0, len(env) - 1)]
        phase = max(
            range(self.meter),
            key=lambda p: float(np.mean(strengths[p::self.meter]))
            if len(strengths[p::self.meter]) else -1.0,
        )
        return beat_times[phase::self.meter]


class BeatThisBeats:
    """Beat tracking con beat_this (ISMIR 2024). Downbeats reales, no inferidos.

    Vive en un venv aparte (ver tools/install_beat_this.ps1) porque arrastra
    torch. Se invoca por subprocess y devuelve JSON, de modo que el core del
    pipeline no depende de torch ni de su version de CUDA.
    """

    name = "beat_this"

    def __init__(self, python: Path | None = None, model: str = "final0") -> None:
        self.python = Path(python) if python else default_beat_this_python()
        self.model = model

    def detect(self, audio: Path) -> Beats:
        if not self.python.exists():
            raise BeatDetectionError(
                f"No existe el interprete de beat_this en {self.python}.\n"
                "Ejecuta:  powershell -ExecutionPolicy Bypass "
                "-File tools/install_beat_this.ps1"
            )
        runner = Path(__file__).parent.parent / "tools" / "beat_this_detect.py"
        proc = subprocess.run(
            [str(self.python), str(runner), str(audio), "--model", self.model],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            raise BeatDetectionError(f"beat_this fallo:\n{proc.stderr[-2000:]}")
        return Beats.from_json(proc.stdout)


def default_beat_this_python() -> Path:
    return Path(__file__).parent.parent / ".venv-beatthis" / "Scripts" / "python.exe"


def get_detector(name: str, bpm_hint: float | None = None) -> BeatDetector:
    """`bpm_hint` orienta la busqueda de tempo. beat_this no lo necesita: su
    modelo de downbeats no sufre el problema de las subdivisiones."""
    if name == "librosa":
        return LibrosaBeats(bpm_hint=bpm_hint)
    if name == "beat_this":
        return BeatThisBeats()
    raise ValueError(f"Detector desconocido: {name}")
