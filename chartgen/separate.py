"""Separacion de fuentes: una mezcla -> un archivo por instrumento.

## Por que esto va antes que transcribir

Un transcriptor de alturas asume una fuente. Sobre la mezcla completa de Rolling
in the Deep, pYIN tiene encima voz, piano y bateria, y el resultado medido contra
el chart de la tablatura fue un F1 del 41%: la mayor parte del error no era de
colocacion sino de contenido, notas inventadas y notas perdidas. Eso no lo
arregla afinar la cuantizacion, porque el problema entra antes.

Es tambien la diferencia real entre "audio a MIDI" en un DAW y hacerlo con un
mp3. Melodyne o Ableton no hacen nada magico: asumen que les das una pista
aislada. Aqui la pista aislada hay que fabricarla.

## Donde vive

Demucs arrastra torch, asi que va en un venv aparte y se invoca por subprocess
intercambiando JSON, igual que `beat_this`. Como los dos son aplicaciones de
torch y sus pines no chocan, por defecto **comparten venv**: `tools/install_demucs.ps1`
instala Demucs encima del que ya creo `install_beat_this.ps1` en vez de bajar
otros 3 GB de torch. Si existe un `.venv-demucs` propio, ese manda.

## Cache

Separar cuesta unos 10 s por minuto de audio en GPU, y el resultado no cambia
nunca para el mismo archivo y modelo. Se guarda en `~/.cache/chartgen/stems` y
se reutiliza. Ocupa: unos 80 MB por cancion de cuatro minutos en cuatro stems.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import audio as audio_mod


class SeparationError(RuntimeError):
    pass


MODEL_SOURCES = {
    "htdemucs": ("drums", "bass", "other", "vocals"),
    "htdemucs_ft": ("drums", "bass", "other", "vocals"),
    "htdemucs_6s": ("drums", "bass", "other", "vocals", "guitar", "piano"),
    "mdx_extra": ("drums", "bass", "other", "vocals"),
}
"""Que stems da cada modelo.

`htdemucs` es el de referencia. `htdemucs_ft` es el mismo afinado, cuatro veces
mas lento. `htdemucs_6s` añade guitarra y piano, que es lo que hara falta cuando
se transcriba guitarra, pero su stem de guitarra es el mas flojo de los seis
segun sus propios autores: para bajo no aporta nada y si quita velocidad."""

INSTRUMENT_STEM = {
    "bass": "bass",
    "drums": "drums",
    # htdemucs no tiene stem de guitarra: va dentro de "other" junto con teclados
    # y cuerdas. Con htdemucs_6s si existe, y el CLI lo elige si se pide ese
    # modelo. Para guitarra distorsionada "other" suele ser suficiente porque en
    # esa banda no compite con casi nada mas.
    "guitar": "other",
    "rhythm": "other",
    "coop": "other",
    "keys": "other",
    "vocals": "vocals",
}
"""Que stem alimenta a cada instrumento del chart."""

DEFAULT_CACHE = Path.home() / ".cache" / "chartgen" / "stems"


@dataclass
class Stems:
    paths: dict[str, Path]
    model: str
    cached: bool = False
    """True si no hizo falta ejecutar nada: ya estaban en disco."""

    def __getitem__(self, name: str) -> Path:
        if name not in self.paths:
            raise SeparationError(
                f"El modelo {self.model} no produjo el stem {name!r}. "
                f"Tiene: {', '.join(sorted(self.paths))}."
            )
        return self.paths[name]

    def describe(self) -> str:
        size = sum(p.stat().st_size for p in self.paths.values() if p.exists())
        estado = "en cache" if self.cached else "separado"
        return (f"{self.model}: {len(self.paths)} stems {estado} "
                f"({size / 1e6:.0f} MB)")


class Separator(Protocol):
    name: str

    def separate(self, audio: Path, model: str) -> Stems: ...


def default_torch_python() -> Path:
    """Interprete del venv con torch. Un venv propio para Demucs tiene prioridad.

    Compartir venv con beat_this es lo normal aqui y es deliberado: los dos son
    aplicaciones de torch, sus dependencias no chocan, y duplicar la instalacion
    cuesta 3 GB para no ganar nada. Quien prefiera aislarlas del todo solo tiene
    que crear `.venv-demucs`; esta funcion lo encuentra sola.
    """
    root = Path(__file__).parent.parent
    for name in (".venv-demucs", ".venv-beatthis"):
        candidate = root / name / "Scripts" / "python.exe"
        if candidate.exists():
            return candidate
    return root / ".venv-demucs" / "Scripts" / "python.exe"


def parse_result(stdout: str) -> dict | None:
    """Saca el JSON de la salida del runner, aunque venga con ruido delante.

    El runner ya redirige lo que Demucs imprime, pero estamos envolviendo un CLI
    ajeno que puede cambiar de opinion sobre por donde habla en cualquier
    version. Paso a la primera: Demucs anunciaba por stdout donde iba a dejar los
    stems, eso se mezclaba con el JSON, y treinta canciones de un banco de
    pruebas fallaron con los archivos ya correctamente escritos en disco.

    Se lee de atras hacia delante porque el JSON es lo ultimo que se imprime.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def cache_key(audio: Path) -> str:
    """Nombre de carpeta para la cache de un archivo concreto.

    El nombre a secas NO sirve, y no es un caso rebuscado: en una biblioteca de
    Clone Hero **todas** las canciones tienen su audio en `song.ogg`. Con la
    primera version, separar la segunda cancion habria devuelto los stems de la
    primera sin ejecutar nada y sin avisar de nada.

    Se añade un hash corto de la ruta absoluta y el tamaño. La fecha no entra:
    copiar la biblioteca de sitio no deberia invalidar 20 GB de stems.
    """
    audio = Path(audio)
    fingerprint = f"{audio.resolve()}|{audio.stat().st_size}".encode("utf-8")
    return f"{audio.stem}-{hashlib.sha1(fingerprint).hexdigest()[:8]}"


class DemucsSeparator:
    """Demucs v4 por subprocess, con cache en disco."""

    name = "demucs"

    def __init__(self, python: Path | None = None,
                 cache_dir: Path | None = None,
                 device: str = "auto", timeout: float = 3600.0) -> None:
        self.python = Path(python) if python else default_torch_python()
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE
        self.device = device
        self.timeout = timeout

    def available(self) -> bool:
        return self.python.exists()

    def expected(self, audio: Path, model: str) -> dict[str, Path]:
        """Donde deberian estar los stems si ya se separo este archivo."""
        folder = self.cache_dir / model / cache_key(audio)
        return {name: folder / f"{name}.flac" for name in MODEL_SOURCES[model]}

    def separate(self, audio: Path, model: str = "htdemucs") -> Stems:
        if model not in MODEL_SOURCES:
            raise SeparationError(f"Modelo desconocido: {model}")
        if not Path(audio).exists():
            raise SeparationError(f"No existe el audio: {audio}")

        cached = self.expected(audio, model)
        if all(p.exists() and p.stat().st_size > 0 for p in cached.values()):
            return Stems(paths=cached, model=model, cached=True)

        if not self.available():
            raise SeparationError(
                f"No existe el interprete con Demucs en {self.python}.\n"
                "Ejecuta:  powershell -ExecutionPolicy Bypass "
                "-File tools/install_demucs.ps1"
            )

        runner = Path(__file__).parent.parent / "tools" / "demucs_separate.py"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            # Se decodifica antes con ffmpeg en vez de darle el mp3 a Demucs.
            # Ver `audio.decode`: dos decodificadores distintos sobre el mismo
            # mp3 no empiezan en la misma muestra, y esa diferencia acaba siendo
            # un sesgo constante en todas las notas del chart. El WAV conserva el
            # nombre del original para que la carpeta de salida sea la esperada.
            decoded = audio_mod.decode(audio, Path(tmp) / f"{cache_key(audio)}.wav")
            return self._run(runner, decoded, model)

    def _run(self, runner: Path, decoded: Path, model: str) -> Stems:
        try:
            proc = subprocess.run(
                [str(self.python), str(runner), str(decoded),
                 "--out", str(self.cache_dir), "--model", model,
                 "--device", self.device],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise SeparationError(
                f"Demucs no termino en {self.timeout:.0f} s. En CPU una cancion "
                "entera puede tardar mucho mas: usa una GPU o recorta el audio."
            ) from None

        if proc.returncode != 0:
            raise SeparationError(f"Demucs fallo:\n{proc.stderr[-2000:]}")
        data = parse_result(proc.stdout)
        if data is None:
            raise SeparationError(
                f"Demucs no devolvio JSON:\n{proc.stdout[-500:]}\n"
                f"{proc.stderr[-1000:]}"
            )

        paths = {name: Path(p) for name, p in data["stems"].items()}
        return Stems(paths=paths, model=data.get("model", model), cached=False)


def stem_for(instrument: str, model: str = "htdemucs") -> str:
    """Que stem hay que darle al transcriptor para cada instrumento del chart."""
    wanted = INSTRUMENT_STEM.get(instrument, "other")
    # Con htdemucs_6s la guitarra tiene stem propio; con los demas cae en "other".
    if instrument in ("guitar", "rhythm", "coop") and "guitar" in MODEL_SOURCES[model]:
        return "guitar"
    if instrument == "keys" and "piano" in MODEL_SOURCES[model]:
        return "piano"
    return wanted
