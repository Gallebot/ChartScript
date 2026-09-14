"""Mide la transcripcion contra una biblioteca entera de charts humanos.

## Por que existe

Hasta ahora todo lo de M5 estaba calibrado contra UNA cancion, la unica con
tablatura y audio a mano. Con una muestra de uno no se distingue un arreglo de
una casualidad, y cualquier ajuste posterior es sobreajuste.

Resulta que la verdad absoluta ya estaba en disco: **164 de los 191 charts de la
coleccion de referencia traen su propio audio**, todos con `delay = 0`, y 44 de
ellos traen ademas los stems por instrumento (`guitar.ogg`, `bass.ogg`...). Eso
es un banco de pruebas de 164 pares (audio, chart humano), y los 44 con stems
permiten medir el transcriptor SIN el error de Demucs de por medio, que es la
unica forma de saber cual de los dos falla cuando falla.

## Que se compara

Tiempos de ataque, no carriles: dos personas charteando la misma cancion no
tienen por que coincidir en el boton. El techo tampoco es el 100%, es el acuerdo
entre dos charters, porque un "fallo" puede ser criterio del humano.

## Coste y cache

Una cancion cuesta del orden de minuto y medio (deteccion de beats, separacion y
pYIN). Se cachea todo lo caro por separado — beats y notas crudas, con clave
sobre el archivo y los parametros — para que reajustar el CUANTIZADOR, que es lo
que se toca a menudo, sea cuestion de segundos y no de horas.

Los resultados se escriben a JSON segun salen: una tanda larga que se corte no
pierde lo hecho, y `--resume` sigue donde estaba.

Uso:
    python tools/benchmark.py --instrument guitar --sample 12
    python tools/benchmark.py --instrument guitar --source own
    python tools/benchmark.py --instrument bass --out resultados-bajo.json
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import random
import statistics
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verify_transcription import SECTION, match  # noqa: E402

from chartgen import PAD_SECONDS, RESOLUTION  # noqa: E402
from chartgen import beats as beats_mod  # noqa: E402
from chartgen import separate as separate_mod  # noqa: E402
from chartgen import tempo_fit, transcribe as transcribe_mod  # noqa: E402
from chartgen.frontends import transcription as tx_frontend  # noqa: E402

DEFAULT_LIBRARY = Path("D:/Clone Hero/Canciones")
DEFAULT_CACHE = Path.home() / ".cache" / "chartgen" / "benchmark"
AUDIO_SUFFIXES = (".ogg", ".mp3", ".opus", ".wav", ".flac")


def safe(text: str) -> str:
    """La consola de Windows no codifica los nombres de muchas canciones."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)


# --------------------------------------------------------------------------
# La biblioteca
# --------------------------------------------------------------------------

@dataclass
class Song:
    folder: Path
    chart: Path
    mix: Path
    stems: dict[str, Path] = field(default_factory=dict)
    """Stems que la propia carpeta trae, si los trae."""

    @property
    def name(self) -> str:
        return self.folder.name


def find_audio(folder: Path, stem: str) -> Path | None:
    for suffix in AUDIO_SUFFIXES:
        candidate = folder / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    return None


def scan(library: Path) -> list[Song]:
    """Canciones con chart y con audio. Sin audio no hay nada que medir."""
    songs = []
    for folder in sorted(library.iterdir()):
        chart = folder / "notes.chart"
        if not folder.is_dir() or not chart.exists():
            continue
        mix = find_audio(folder, "song")
        if mix is None:
            continue
        stems = {}
        for name in ("guitar", "bass", "drums", "vocals", "rhythm", "keys"):
            found = find_audio(folder, name)
            if found is not None:
                stems[name] = found
        songs.append(Song(folder=folder, chart=chart, mix=mix, stems=stems))
    return songs


def reference_times(chart: Path, instrument: str) -> list[float]:
    """Tiempos de nota del chart humano, en segundos sobre su propio audio.

    Sin descontar `PAD_SECONDS`: estos charts no llevan lead-in nuestro, son
    ajenos y su `song.ogg` es la linea de tiempo. Todos los de la coleccion
    tienen `delay = 0`, asi que no hay nada mas que corregir.
    """
    from verify_alignment import chart_note_times

    # `chart_note_times` es una herramienta de linea de comandos y avisa saliendo
    # con SystemExit, que NO hereda de Exception: sin esto, la primera cancion sin
    # ese instrumento tumbaba la tanda entera en vez de contarse como un salto.
    try:
        return sorted(chart_note_times(chart, SECTION[instrument], pad=0.0))
    except SystemExit as exc:
        raise RuntimeError(f"no hay pista de {instrument}: {exc}") from None


# --------------------------------------------------------------------------
# Cache de lo caro
# --------------------------------------------------------------------------

def fingerprint(path: Path, **params) -> str:
    stat = path.stat()
    parts = f"{path.resolve()}|{stat.st_size}|" + "|".join(
        f"{k}={v}" for k, v in sorted(params.items()))
    return hashlib.sha1(parts.encode("utf-8")).hexdigest()[:16]


class Cache:
    def __init__(self, root: Path | None, enabled: bool = True) -> None:
        self.root = Path(root) if root else DEFAULT_CACHE
        self.enabled = enabled

    def path(self, kind: str, key: str) -> Path:
        return self.root / kind / f"{key}.json"

    def load(self, kind: str, key: str) -> str | None:
        target = self.path(kind, key)
        if not self.enabled or not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    def save(self, kind: str, key: str, text: str) -> None:
        if not self.enabled:
            return
        target = self.path(kind, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# El pipeline, por cancion
# --------------------------------------------------------------------------

def detect_beats(mix: Path, detector: str, cache: Cache) -> beats_mod.Beats:
    key = fingerprint(mix, detector=detector)
    cached = cache.load("beats", key)
    if cached:
        return beats_mod.Beats.from_json(cached)
    detected = beats_mod.get_detector(detector).detect(mix)
    cache.save("beats", key, detected.to_json())
    return detected


def pick_source(song: Song, instrument: str, mode: str, model: str,
                separator: separate_mod.DemucsSeparator) -> tuple[Path, str]:
    """De donde salen las notas: el stem propio, el de Demucs, o la mezcla."""
    own = song.stems.get(instrument)
    if mode == "mix":
        return song.mix, "mezcla"
    if mode == "own":
        if own is None:
            raise RuntimeError(f"la carpeta no trae stem de {instrument}")
        return own, "propio"
    if mode == "auto" and own is not None:
        return own, "propio"
    stems = separator.separate(song.mix, model)
    return stems[separate_mod.stem_for(instrument, model)], f"demucs/{model}"


ATTACK_TO_DENSITY = (0.40, 14.04)
"""Recta humana ~ a + b * fraccion_de_ataque, ajustada sobre 30 canciones.

Ver `attack_share`. Es un ajuste hecho AQUI, en la herramienta de medida, y no en
el pipeline: primero se comprueba si la idea gana, y solo despues se lleva a
produccion con la fontaneria que haga falta."""


def attack_share(source: Path, cache: Cache) -> float:
    """Fraccion de cuadros donde el ataque supera el doble de la mediana.

    Es una medida POR CANCION y sin normalizar por nota: mide cuanto ataque tiene
    el tema en conjunto, que es justo lo que la salience de cada nota no puede
    decir porque esta dividida por la mediana de la propia cancion.

    Correlacion de rangos con la densidad del chart humano: +0.74. La densidad
    que generamos nosotros correlaciona +0.15, o sea nada.
    """
    key = fingerprint(source, kind="attack")
    cached = cache.load("attack", key)
    if cached:
        return json.loads(cached)["share"]

    import librosa
    import numpy as np

    y, sr = librosa.load(str(source), sr=22050, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256)
    positive = env[env > 0]
    median = float(np.median(positive)) if len(positive) else 1.0
    share = float((env > 2 * median).mean())
    cache.save("attack", key, json.dumps({"share": share}))
    return share


def predicted_ceiling(source: Path, cache: Cache) -> float:
    a, b = ATTACK_TO_DENSITY
    return float(min(8.0, max(0.5, a + b * attack_share(source, cache))))


def transcribe(source: Path, transcriber_name: str,
               cache: Cache) -> list[transcribe_mod.TranscribedNote]:
    key = fingerprint(source, transcriber=transcriber_name,
                      version=transcribe_mod.CACHE_VERSION)
    cached = cache.load("notes", key)
    if cached:
        return transcribe_mod.from_json(cached)
    detected = transcribe_mod.get_transcriber(transcriber_name).transcribe(source)
    cache.save("notes", key, transcribe_mod.to_json(detected))
    return detected


def nearest_bias(found: list[float], truth: list[float],
                 window: float = 0.15) -> float:
    """Sesgo mediano de cada nota generada respecto a la humana MAS CERCANA.

    Es el diagnostico que separa "el chart esta corrido" de "el chart esta mal",
    y no puede salir del emparejamiento uno a uno que da el F1. Con 345 notas
    generadas frente a 727 humanas, el emparejador secuencial se desincroniza y
    empieza a casar notas que no se corresponden: medido asi daba +66 ms donde
    el sesgo real eran +14. Aqui cada nota mira a su vecina y no arrastra a nadie.
    """
    if not found or not truth:
        return 0.0
    errors = []
    for time in found:
        index = bisect.bisect_left(truth, time)
        for candidate in truth[max(0, index - 1):index + 1]:
            if abs(time - candidate) <= window:
                errors.append((time - candidate) * 1000.0)
                break
    return statistics.median(errors) if errors else 0.0


def run_song(song: Song, args, cache: Cache,
             separator: separate_mod.DemucsSeparator) -> dict:
    started = time.time()
    truth = reference_times(song.chart, args.instrument)
    if len(truth) < 20:
        raise RuntimeError(f"el chart humano solo tiene {len(truth)} notas de "
                           f"{args.instrument}")

    detected = detect_beats(song.mix, args.detector, cache)
    fit = tempo_fit.fit(detected, max_error=args.max_error)

    # Cuanto encajan las notas del chart HUMANO en nuestra rejilla. Mide la
    # calidad del mapa de tempo sin que la transcripcion se meta por medio: si el
    # tempo esta bien, unas notas escritas por una persona sobre una rejilla
    # musical tienen que caer sobre la nuestra. Se toma la mejor subdivision para
    # no penalizar a quien va a tresillos.
    padded = [time + PAD_SECONDS for time in truth]
    agreement = max(
        tx_frontend.grid_share(padded, fit.tempo_map, fit.origin_tick,
                               RESOLUTION // steps, args.tolerance)
        for steps in tx_frontend.SUBDIVISIONS)

    source, origin = pick_source(song, args.instrument, args.source, args.model,
                                 separator)
    raw = transcribe(source, args.transcriber, cache)
    ceiling = predicted_ceiling(source, cache) if args.ceiling == "auto" else (
        None if args.ceiling == "fijo" else float(args.ceiling))

    chart, report = tx_frontend.convert(
        raw, fit.tempo_map, fit.origin_tick, instrument=args.instrument,
        star_power=False, tolerance=args.tolerance, subdivision=args.subdivision,
        min_salience=args.min_salience, density_ceiling=ceiling,
    )
    track = chart.track(args.instrument)
    # `convert` deja las notas en la linea de tiempo con lead-in; el chart humano
    # no lo tiene, asi que se descuenta para comparar sobre el mismo audio.
    found = sorted({fit.tempo_map.seconds_at_tick(n.tick) - PAD_SECONDS
                    for n in track.notes})

    def score(tolerance: float):
        pairs, _, _ = match(found, truth, tolerance)
        precision = len(pairs) / len(found) if found else 0.0
        recall = len(pairs) / len(truth) if truth else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if precision + recall else 0.0)
        errors = [(a - b) * 1000.0 for a, b in pairs]
        return precision, recall, f1, errors

    precision, recall, f1, errors = score(args.tolerance_match)
    _, recall_wide, f1_wide, _ = score(2 * args.tolerance_match)

    return {
        "song": song.name,
        "ok": True,
        "source": origin,
        "notes_found": len(found),
        "notes_truth": len(truth),
        "raw_notes": len(raw),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "bias_ms": statistics.median(errors) if errors else 0.0,
        "f1_wide": f1_wide,
        "recall_wide": recall_wide,
        "nearest_bias_ms": nearest_bias(found, truth),
        "subdivision": tx_frontend.label(report.subdivision),
        "off_grid_share": report.off_grid_share,
        "lag_ms": report.lag_ms,
        "tempo_reliable": fit.is_reliable,
        "tempo_agreement": agreement,
        "mean_residual_ms": fit.mean_residual * 1000.0,
        "median_residual_ms": fit.median_residual * 1000.0,
        "max_residual_ms": fit.max_residual * 1000.0,
        "over_budget_share": fit.beats_over_budget / max(1, len(fit.residuals)),
        "tempo_events": fit.tempo_events,
        "ceiling": ceiling,
        "beats": len(fit.beat_ticks),
        "bpm": round(detected.median_bpm, 1),
        "seconds": round(time.time() - started, 1),
    }


# --------------------------------------------------------------------------
# Informe
# --------------------------------------------------------------------------

def summarize(rows: list[dict], args) -> None:
    good = [r for r in rows if r.get("ok")]
    bad = [r for r in rows if not r.get("ok")]

    print()
    print("=" * 78)
    print(f"{args.instrument} | fuente {args.source} | tolerancia "
          f"{args.tolerance_match * 1000:.0f} ms | {len(good)} canciones "
          f"({len(bad)} fallaron)")
    print("=" * 78)
    if not good:
        for row in bad[:10]:
            print(f"  {safe(row['song'])[:50]:<52} {row.get('error', '')[:60]}")
        return

    f1s = sorted(r["f1"] for r in good)

    def pct(fraction: float) -> float:
        return f1s[min(len(f1s) - 1, int(len(f1s) * fraction))]

    print(f"  F1 mediana : {statistics.median(f1s):.1%}")
    print(f"  F1 media   : {statistics.mean(f1s):.1%}")
    print(f"  p10 / p90  : {pct(0.10):.1%} / {pct(0.90):.1%}")
    print(f"  peor/mejor : {f1s[0]:.1%} / {f1s[-1]:.1%}")
    print()
    print(f"  precision  : {statistics.median(r['precision'] for r in good):.1%} "
          f"(mediana)")
    print(f"  recall     : {statistics.median(r['recall'] for r in good):.1%}")
    print(f"  F1 al doble de tolerancia: "
          f"{statistics.median(r['f1_wide'] for r in good):.1%}")
    print(f"  sesgo      : "
          f"{statistics.median(r['nearest_bias_ms'] for r in good):+.1f} ms "
          f"(contra la nota humana mas cercana)")
    corridas = [r for r in good if abs(r["nearest_bias_ms"]) > 25]
    if corridas:
        print(f"  AVISO: {len(corridas)} canciones con sesgo > 25 ms. Eso es un "
              "chart corrido, no mal transcrito.")
    print(f"  notas      : {sum(r['notes_found'] for r in good):,} generadas / "
          f"{sum(r['notes_truth'] for r in good):,} humanas")
    print(f"  desfase    : {statistics.median(r['lag_ms'] for r in good):+.1f} ms "
          f"(mediana del corregido)")
    print(f"  fuera de rejilla: "
          f"{statistics.median(r['off_grid_share'] for r in good):.0%}")

    grids: dict[str, int] = {}
    for row in good:
        grids[row["subdivision"]] = grids.get(row["subdivision"], 0) + 1
    print("  rejillas   : " + ", ".join(
        f"{k} x{v}" for k, v in sorted(grids.items(), key=lambda kv: -kv[1])))

    if "tempo_agreement" in good[0]:
        print(f"  el chart humano encaja en nuestra rejilla un "
              f"{statistics.median(r['tempo_agreement'] for r in good):.0%} "
              f"(mediana)")

    shaky = [r["f1"] for r in good if not r["tempo_reliable"]]
    solid = [r["f1"] for r in good if r["tempo_reliable"]]
    if shaky and solid:
        print()
        print(f"  tempo mal modelado en {len(shaky)}: F1 mediana "
              f"{statistics.median(shaky):.1%} frente a "
              f"{statistics.median(solid):.1%} del resto")

    print()
    print("  peores:")
    for row in sorted(good, key=lambda r: r["f1"])[:5]:
        print(f"    {row['f1']:>6.1%}  {safe(row['song'])[:44]:<46} "
              f"{row['notes_found']:>5}/{row['notes_truth']:<5} "
              f"{row['subdivision']}")
    print("  mejores:")
    for row in sorted(good, key=lambda r: -r["f1"])[:5]:
        print(f"    {row['f1']:>6.1%}  {safe(row['song'])[:44]:<46} "
              f"{row['notes_found']:>5}/{row['notes_truth']:<5} "
              f"{row['subdivision']}")

    if bad:
        print()
        print(f"  fallos ({len(bad)}):")
        reasons: dict[str, int] = {}
        for row in bad:
            reason = row.get("error", "?").split("\n")[0][:60]
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    x{count:<3} {safe(reason)}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    ap.add_argument("--instrument", default="guitar", choices=sorted(SECTION))
    ap.add_argument("--source", default="auto",
                    choices=["auto", "own", "demucs", "mix"],
                    help="'own' usa los stems que trae la carpeta (mide el "
                         "transcriptor sin el error de Demucs); 'auto' los usa "
                         "si estan y separa si no.")
    ap.add_argument("--model", default="htdemucs",
                    choices=sorted(separate_mod.MODEL_SOURCES))
    ap.add_argument("--transcriber", default="pyin")
    ap.add_argument("--detector", default="beat_this",
                    choices=["librosa", "beat_this"])
    ap.add_argument("--subdivision", type=int, default=None,
                    choices=list(tx_frontend.SUBDIVISIONS))
    ap.add_argument("--tolerance", type=float,
                    default=tx_frontend.DEFAULT_TOLERANCE,
                    help="Tolerancia de la CUANTIZACION, en segundos.")
    ap.add_argument("--min-salience", type=float, default=None)
    ap.add_argument("--ceiling", default="fijo",
                    help="Techo de densidad: 'fijo' (el global por instrumento), "
                         "'auto' (predicho por cancion desde el ataque), o un "
                         "numero en eventos/s.")
    ap.add_argument("--tolerance-match", type=float, default=0.05,
                    help="Ventana de acierto al comparar con el chart humano.")
    ap.add_argument("--max-error", type=float, default=tempo_fit.DEFAULT_MAX_ERROR)
    ap.add_argument("--sample", type=int, default=None,
                    help="Coge N canciones al azar en vez de todas.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--filter", default=None,
                    help="Solo carpetas que contengan este texto.")
    ap.add_argument("--out", type=Path, default=Path("benchmark.json"))
    ap.add_argument("--resume", action="store_true",
                    help="Salta las canciones que ya esten en --out.")
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    if not args.library.exists():
        print(f"No existe la biblioteca: {args.library}", file=sys.stderr)
        return 2

    songs = scan(args.library)
    if args.filter:
        needle = args.filter.lower()
        songs = [s for s in songs if needle in s.name.lower()]
    if args.source == "own":
        songs = [s for s in songs if args.instrument in s.stems]
    if args.sample:
        random.Random(args.seed).shuffle(songs)
        songs = songs[:args.sample]
    if args.limit:
        songs = songs[:args.limit]

    rows: list[dict] = []
    done: set[str] = set()
    if args.resume and args.out.exists():
        previous = json.loads(args.out.read_text(encoding="utf-8"))["rows"]
        # Los fallos NO cuentan como hechos: reanudar despues de arreglar el
        # motivo del fallo es justo cuando hace falta reanudar.
        rows = [r for r in previous if r.get("ok")]
        done = {r["song"] for r in rows}
        retry = len(previous) - len(rows)
        print(f"Reanudando: {len(done)} medidas, {retry} fallos que se reintentan.")

    cache = Cache(args.cache_dir, enabled=not args.no_cache)
    separator = separate_mod.DemucsSeparator()
    pending = [s for s in songs if s.name not in done]
    print(f"{len(pending)} canciones por medir "
          f"(instrumento {args.instrument}, fuente {args.source}).")

    for index, song in enumerate(pending, 1):
        label = safe(song.name)[:44]
        print(f"[{index:>3}/{len(pending)}] {label:<46} ", end="", flush=True)
        try:
            row = run_song(song, args, cache, separator)
            print(f"F1 {row['f1']:>6.1%}  {row['notes_found']:>4}/"
                  f"{row['notes_truth']:<4} {row['subdivision']:<5} "
                  f"{row['seconds']:>5.1f}s  [{row['source']}]")
        except KeyboardInterrupt:
            print("\ninterrumpido")
            break
        except Exception as exc:                      # noqa: BLE001
            row = {"song": song.name, "ok": False,
                   "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc()[-1500:]}
            print(f"FALLO  {safe(str(exc))[:44]}")
        rows.append(row)
        args.out.write_text(json.dumps(
            {"args": {k: str(v) for k, v in vars(args).items()}, "rows": rows},
            indent=1, ensure_ascii=False), encoding="utf-8")

    summarize(rows, args)
    print()
    print(f"Resultados en {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
