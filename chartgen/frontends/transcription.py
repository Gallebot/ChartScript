"""Frontend de audio: notas transcritas -> IR.

## Donde esta aqui la fidelidad

No en el transcriptor. Un detector devuelve ataques con 20 o 30 ms de error y
alturas con algun fallo suelto; si esas notas se escriben tal cual, el chart se
siente sucio aunque cada nota este "casi" bien. Lo que lo arregla es cuantizar
contra el mapa de tempo que M2 ajusto A ESTA GRABACION, que ya respira con ella.

Cuantizar contra un BPM fijo seria peor que no cuantizar: acumula el mismo error
que M3c existe para corregir. Aqui la rejilla se deriva del `TempoMap`, asi que
sigue las variaciones de tempo de la cancion sin que nadie tenga que decirselo.

## Las dos decisiones que importan

**Que subdivision.** No se fija a mano. Una rejilla mas fina SIEMPRE explica
mejor los datos (a 1/32 no hay nada fuera de rejilla), asi que elegir por error
minimo lleva siempre a la mas fina y a cuantizar el ruido. El criterio es al
reves: la subdivision MAS GRUESA que explique la mayoria de los ataques.

**Que hacer con lo que no encaja.** Arrastrar a la rejilla un ataque que cae a
80 ms del pulso mas cercano convierte un fallo del detector en una nota jugable
en un sitio donde no hay nada. Aqui esas notas se dejan donde estaban, sin
cuantizar, y se cuentan: si son muchas, el problema no es la rejilla sino el
mapa de tempo o el detector, y hay que decirlo en vez de disimularlo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import PAD_SECONDS, RESOLUTION
from ..charting import MAX_CHORD, RawNote, add_star_power, build_notes, map_lanes
from ..ir import Metadata, Song, Track
from ..tempo import TempoMap
from ..transcribe import TranscribedNote, clean


class TranscriptionFrontendError(RuntimeError):
    pass


SUBDIVISIONS = (1, 2, 3, 4, 6, 8)
"""Pasos por negra que se prueban: negra, corchea, tresillo, semicorchea,
tresillo de semicorchea y fusa.

Los tresillos (3 y 6) estan porque una rejilla binaria los coloca a mitad de
camino de dos pasos, que es justo el caso en el que la cuantizacion estropea lo
que el detector habia acertado. Mas fino que la fusa no se prueba: a esa altura
la rejilla ya no restringe nada y cuantizar deja de aportar."""

DEFAULT_TOLERANCE = 0.045
"""Segundos que puede moverse un ataque para considerarlo dentro de la rejilla.

Es el presupuesto de error del detector, no el de lo que se oye: el ajuste de
tempo trabaja con 10 ms y un detector de onsets razonable acierta dentro de 30.
Con 45 ms se acepta esa dispersion sin llegar a la mitad de un paso de fusa a
tempos normales, que es donde el criterio dejaria de distinguir rejillas."""

MAX_LAG = 0.06
"""Segundos. Tope del desfase global que se corrige solo.

Medido sobre Rolling in the Deep, las notas del stem de bajo caen 37 ms por
detras de donde el chart de referencia las pone. No es un fallo del detector: se
reproduce con cualquier banda de frecuencias y con ventanas de analisis de 12 a
93 ms. Es que **el bajo de esa grabacion suena detras del golpe de bateria** que
define el pulso, y sobre la mezcla completa el ataque que se detecta es el de la
bateria (ahi el sesgo baja a 14 ms).

Para un chart eso es un problema, no una virtud: los charts humanos van a la
rejilla. Y ademas 37 ms es medio paso de una rejilla de 1/32, o sea justo lo que
hace falta para mandar media transcripcion al pulso de al lado.

Por encima de este tope no se corrige nada y se avisa: un desfase de 100 ms ya no
es el feel del bajista, es el mapa de tempo mal ajustado o el audio corrido, y
taparlo con una traslacion global seria esconder el problema."""

MIN_SHARE = 0.8
"""Fraccion de ataques que una subdivision tiene que explicar para valer.

No se pide el 100% porque siempre hay adornos fuera de rejilla y notas mal
detectadas; pedirlo llevaria siempre a la rejilla mas fina, que es justo lo que
se quiere evitar."""


def label(steps: int) -> str:
    """Nombre musical de una subdivision, en fracciones de redonda."""
    return f"1/{steps * 4}"


def nearest_grid_tick(seconds: float, tempo_map: TempoMap, origin_tick: int,
                      step: int) -> tuple[int, float]:
    """Tick de rejilla mas cercano y a cuantos segundos queda.

    La busqueda se hace en TIEMPO y no en ticks: con un cambio de tempo en medio,
    dos ticks equidistantes no estan a la misma distancia en segundos, y lo que
    se oye es la distancia en segundos.
    """
    approximate = tempo_map.tick_at_seconds(max(0.0, seconds))
    index = round((approximate - origin_tick) / step)
    best_tick, best_error = None, float("inf")
    for candidate in (index - 1, index, index + 1):
        tick = origin_tick + candidate * step
        if tick < 0:
            continue
        error = abs(tempo_map.seconds_at_tick(tick) - seconds)
        if error < best_error:
            best_tick, best_error = tick, error
    if best_tick is None:
        return 0, abs(tempo_map.seconds_at_tick(0) - seconds)
    return best_tick, best_error


def grid_share(times: list[float], tempo_map: TempoMap, origin_tick: int,
               step: int, tolerance: float) -> float:
    """Fraccion de ataques que caen dentro de la tolerancia de esta rejilla."""
    if not times:
        return 0.0
    step_seconds = step * 60.0 / (tempo_map.bpm_at_tick(origin_tick)
                                  * tempo_map.resolution)
    # Nunca se acepta mas de un 40% del paso: sin este tope, a tempos rapidos la
    # tolerancia absoluta se acerca a medio paso y entonces TODO cae "dentro",
    # tambien lo que esta justo entre dos pulsos.
    limit = min(tolerance, 0.4 * step_seconds)
    inside = sum(1 for t in times
                 if nearest_grid_tick(t, tempo_map, origin_tick, step)[1] <= limit)
    return inside / len(times)


def estimate_lag(times: list[float], tempo_map: TempoMap, origin_tick: int,
                 step: int, cap: float = MAX_LAG) -> float:
    """Cuanto van los ataques por detras (o por delante) de la rejilla.

    La MEDIANA del residuo con signo. Si todas las notas llegan tarde por igual,
    la mediana lo recoge; si estan repartidas al azar, se queda en cero y no
    mueve nada. No hace falta ninguna verdad absoluta: la rejilla ya la da el
    mapa de tempo, y lo que se mide es el sesgo del detector contra ella.

    Ojo con el paso que se le pasa: el residuo esta acotado a medio paso, asi que
    una rejilla demasiado fina no puede ni representar el desfase que se busca.
    Por eso `quantize` lo estima sobre la rejilla que eligio, y no sobre la mas
    fina de todas.
    """
    if not times:
        return 0.0
    import statistics

    residuals = [time - tempo_map.seconds_at_tick(
        nearest_grid_tick(time, tempo_map, origin_tick, step)[0]) for time in times]
    return max(-cap, min(cap, statistics.median(residuals)))


def lag_step(tempo_map: TempoMap, origin_tick: int, cap: float = MAX_LAG) -> int:
    """Sobre que rejilla se puede preguntar por el desfase global.

    Un residuo contra una rejilla esta acotado a medio paso: si el paso es de
    71 ms, un retraso de 37 ms se confunde con un adelanto de 34 y la mediana
    se va a cero. Fue exactamente lo que paso en la primera version, que
    estimaba sobre la rejilla elegida y devolvia +1 ms donde habia 37.

    Asi que se pregunta a la rejilla mas fina cuyo paso llegue al doble del
    desfase maximo admisible. A 105 BPM eso son las semicorcheas (143 ms).
    """
    best = SUBDIVISIONS[0]
    for steps in SUBDIVISIONS:
        step_seconds = (RESOLUTION // steps) * 60.0 / (
            tempo_map.bpm_at_tick(origin_tick) * tempo_map.resolution)
        if step_seconds >= 2 * cap:
            best = steps
    return best


def choose_subdivision(times: list[float], tempo_map: TempoMap, origin_tick: int,
                       tolerance: float = DEFAULT_TOLERANCE,
                       min_share: float = MIN_SHARE,
                       lag: float = 0.0) -> tuple[int, dict[int, float]]:
    """La subdivision mas gruesa que explica `min_share` de los ataques."""
    times = [t - lag for t in times]
    shares = {}
    for steps in SUBDIVISIONS:
        step = RESOLUTION // steps
        shares[steps] = grid_share(times, tempo_map, origin_tick, step, tolerance)

    for steps in SUBDIVISIONS:
        if shares[steps] >= min_share:
            return steps, shares
    # Ninguna llega: manda la que mejor lo haga, y el aviso lo da el informe.
    return max(shares, key=lambda s: shares[s]), shares


@dataclass
class Quantization:
    """Que hizo la cuantizacion. Se informa siempre, salga bien o mal."""

    subdivision: int
    shares: dict[int, float]
    total: int
    snapped: int
    off_grid: int
    shifts_ms: list[float] = field(default_factory=list)
    """Cuanto se movio cada nota que si encajaba."""
    lag_ms: float = 0.0
    """Desfase global que se corrigio antes de encajar."""
    lag_clipped: bool = False

    @property
    def median_shift_ms(self) -> float:
        if not self.shifts_ms:
            return 0.0
        ordered = sorted(self.shifts_ms)
        return ordered[len(ordered) // 2]

    @property
    def off_grid_share(self) -> float:
        return self.off_grid / self.total if self.total else 0.0

    @property
    def snapped_share(self) -> float:
        return self.snapped / self.total if self.total else 0.0

    def describe(self) -> str:
        # La fraccion sale de lo que realmente encajo, no de `shares`: cuando la
        # subdivision se fija con --subdivision no hay busqueda que informar.
        return (
            f"rejilla {label(self.subdivision)} | {self.total} notas, "
            f"{self.snapped} encajadas ({self.snapped_share:.0%}), "
            f"{self.off_grid} fuera | desfase global {self.lag_ms:+.1f} ms | "
            f"desplazamiento mediano {self.median_shift_ms:.1f} ms"
        )

    def warnings(self) -> list[str]:
        out = []
        if self.off_grid_share > 0.25:
            out.append(
                f"El {self.off_grid_share:.0%} de las notas no encaja en ninguna "
                "rejilla. Eso no lo arregla cuantizar: o el mapa de tempo esta "
                "mal ajustado, o el detector esta inventando ataques."
            )
        if self.lag_clipped:
            out.append(
                f"El desfase global se topo en {MAX_LAG * 1000:.0f} ms. A partir "
                "de ahi ya no es el feel del interprete: mira el mapa de tempo y "
                "si el audio que se transcribio empieza donde el que se empaqueta."
            )
        if self.subdivision == SUBDIVISIONS[-1]:
            out.append(
                f"Se eligio la rejilla mas fina ({label(self.subdivision)}). "
                "Suele indicar que los ataques estan dispersos y no que la "
                "cancion vaya a fusas; el chart va a salir irregular."
            )
        return out


def quantize(notes: list[TranscribedNote], tempo_map: TempoMap, origin_tick: int,
             subdivision: int | None = None, tolerance: float = DEFAULT_TOLERANCE,
             min_share: float = MIN_SHARE,
             lag: float | None = None) -> tuple[list[RawNote], Quantization]:
    """Lleva las notas a ticks, encajando en la rejilla lo que se deje.

    Las notas llegan en segundos sobre la MISMA linea de tiempo que el mapa de
    tempo. Si vienen del audio original hay que sumarles el lead-in antes.
    """
    if not notes:
        raise TranscriptionFrontendError("No hay notas que cuantizar.")

    times = [n.start for n in notes]
    shares: dict[int, float] = {}

    # El desfase se mide ANTES de elegir rejilla, y sobre una rejilla gruesa
    # elegida solo para poder medirlo. Al reves no funciona: un sesgo de medio
    # paso es justo lo que empuja a elegir la rejilla mas fina, y esa ya no puede
    # representar el sesgo que la provoco.
    if lag is None:
        lag = estimate_lag(times, tempo_map, origin_tick,
                           RESOLUTION // lag_step(tempo_map, origin_tick))
    if subdivision is None:
        subdivision, shares = choose_subdivision(times, tempo_map, origin_tick,
                                                 tolerance, min_share, lag)

    clipped = abs(abs(lag) - MAX_LAG) < 1e-9
    times = [t - lag for t in times]
    notes = [TranscribedNote(n.start - lag, n.end - lag, n.pitch, n.confidence)
             for n in notes]
    step = RESOLUTION // subdivision
    step_seconds = step * 60.0 / (tempo_map.bpm_at_tick(origin_tick)
                                  * tempo_map.resolution)
    limit = min(tolerance, 0.4 * step_seconds)

    raw: list[RawNote] = []
    snapped = off_grid = 0
    shifts: list[float] = []

    for note in notes:
        tick, error = nearest_grid_tick(note.start, tempo_map, origin_tick, step)
        if error <= limit:
            snapped += 1
            shifts.append(error * 1000.0)
        else:
            # Se queda donde estaba. Su tiempo es informacion real del audio;
            # la rejilla es una hipotesis que en este punto no se cumple.
            tick = tempo_map.tick_at_seconds(max(0.0, note.start))
            off_grid += 1

        end_tick, end_error = nearest_grid_tick(note.end, tempo_map, origin_tick,
                                                step)
        if end_error > limit or end_tick <= tick:
            end_tick = max(tick + step, tempo_map.tick_at_seconds(max(0.0, note.end)))

        raw.append(RawNote(tick=tick, pitch=note.midi, length=end_tick - tick,
                           pitches=(note.midi,)))

    raw.sort(key=lambda n: (n.tick, n.pitch))
    report = Quantization(subdivision=subdivision, shares=shares, total=len(notes),
                          snapped=snapped, off_grid=off_grid, shifts_ms=shifts,
                          lag_ms=lag * 1000.0, lag_clipped=clipped)
    return raw, report


def group_chords(raw: list[RawNote]) -> list[RawNote]:
    """Une en una sola nota las que acabaron en el mismo tick.

    Despues de cuantizar, dos notas del mismo instante son un acorde. En una
    fuente monofonica son un error del detector, pero no hace falta decidirlo
    aqui: `build_notes` con `max_chord=1` se queda con la mas grave, que es la
    misma regla que usa el frontend de tablatura para el bajo.
    """
    grouped: dict[int, list[RawNote]] = {}
    for note in raw:
        grouped.setdefault(note.tick, []).append(note)

    out: list[RawNote] = []
    for tick in sorted(grouped):
        group = grouped[tick]
        pitches = tuple(sorted({n.pitch for n in group}))
        out.append(RawNote(tick=tick, pitch=pitches[0],
                           length=max(n.length for n in group), pitches=pitches))
    return out


def convert(notes: list[TranscribedNote], tempo_map: TempoMap, origin_tick: int,
            instrument: str = "bass", pad_seconds: float = PAD_SECONDS,
            difficulty: str = "Expert", star_power: bool = True,
            subdivision: int | None = None, tolerance: float = DEFAULT_TOLERANCE,
            cleanup: bool = True, max_chord: int | None = None,
            metadata: Metadata | None = None,
            lag: float | None = None,
            min_salience: float | None = None,
            density_ceiling: float | None = None) -> tuple[Song, Quantization]:
    """Notas transcritas del audio ORIGINAL -> Song listo para empaquetar."""
    if not notes:
        raise TranscriptionFrontendError("El transcriptor no devolvio ninguna nota.")

    if cleanup:
        notes = clean(notes, instrument=instrument, ceiling=density_ceiling,
                      **({} if min_salience is None
                         else {"min_salience": min_salience}))
        if not notes:
            raise TranscriptionFrontendError(
                "La limpieza descarto todas las notas. Suele significar que el "
                "detector devolvio ruido, no una linea instrumental."
            )

    # El mapa de tempo vive en el audio ya padeado; la transcripcion sale del
    # original. Es el mismo desfase que descuenta el encaje de tablaturas.
    shifted = [TranscribedNote(n.start + pad_seconds, n.end + pad_seconds,
                               n.pitch, n.confidence) for n in notes]

    raw, report = quantize(shifted, tempo_map, origin_tick, subdivision, tolerance,
                           lag=lag)
    raw = group_chords(raw)

    if max_chord is None:
        max_chord = 1 if instrument in ("bass", "vocals") else MAX_CHORD

    lanes = map_lanes(raw)
    built, _ = build_notes(raw, lanes, max_chord=max_chord)

    # Sin `force`: el frontend de tablatura lo pone donde la tab marca ligado, y
    # una transcripcion no sabe si el bajista ligo o volvio a pulsar. Medido
    # sobre 192 charts reales, los humanos dejan vivir el HOPO automatico del
    # juego la mayoria de las veces, asi que no escribir nada es lo que mas se
    # parece a lo que harian.
    track = Track(instrument=instrument, difficulty=difficulty)
    track.notes = built

    md = metadata or Metadata(charter="chartgen/transcribe")
    setattr(md, f"diff_{instrument}", 3)

    song = Song(metadata=md, tempo_map=tempo_map, origin_tick=origin_tick)
    song.add_track(track)
    if star_power:
        add_star_power(track, tempo_map, origin_tick)
    return song, report
