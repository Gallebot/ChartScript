"""Ajuste de un mapa de tempo a partir de beats detectados.

El problema no es detectar beats: es decidir cuantos eventos `B` emitir. Uno por
beat da un SyncTrack exacto pero de 800 eventos, imposible de editar en
Moonscraper. Uno solo descuadra cualquier cancion que respire.

El criterio que se usa aqui NO es la diferencia de BPM sino el **error temporal
acumulado**: se extiende un segmento de tempo constante mientras la desviacion
entre el beat predicho y el detectado se mantenga por debajo de un umbral en
segundos. Un error de 0.3 BPM es irrelevante en cuatro beats y descoloca la
cancion entera en doscientos; solo mirando segundos acumulados se distingue.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import PAD_SECONDS, RESOLUTION
from .beats import Beats  # noqa: F401
from .tempo import TempoEvent, TempoMap, TimeSignature, quantize_bpm

DEFAULT_MAX_ERROR = 0.010
"""Segundos. Es el error SISTEMATICO que el ajuste añade sobre el del detector.

El primer valor fue 25 ms, razonando sobre cuando se nota un desajuste al jugar.
Estaba mal planteado: esto no es el error total tolerable sino un sesgo que se
SUMA al del beat tracking, y encima acota directamente donde acaban las notas.

Medido sobre una tablatura real de 3.6 min con beats exactos, el error de
colocacion sigue al presupuesto casi 1:1 y los eventos de tempo suben despacio:

| presupuesto | eventos | error mediano |
|---|---|---|
| 4 ms | 15 | 3.1 ms |
| 10 ms | 10 | 7.5 ms |
| 25 ms | 7 | 17.4 ms |
| 50 ms | 5 | 39.4 ms |

Bajar a 10 ms cuesta tres eventos y divide el error por dos y medio. Un
SyncTrack de diez eventos se edita a mano igual de bien que uno de siete."""

MIN_SEGMENT_BEATS = 4
"""Un segmento mas corto que un compas suele ser ruido del detector."""

RELIABLE_RESIDUAL_FACTOR = 3.5
"""Cuantas veces el presupuesto puede valer el residuo MEDIO antes de avisar.

El criterio anterior era "no mas del 2% de los beats fuera de presupuesto", y
medido sobre 30 canciones de la coleccion de referencia daba **False en las
treinta**. Como senal binaria no distinguia nada.

Para calibrarlo hacia falta saber cuando un mapa de tempo es malo DE VERDAD, y
eso se puede medir sin opinar: se toman las notas del chart humano, que estan
escritas sobre una rejilla musical, y se mira que fraccion cae sobre la rejilla
que deduce nuestro mapa. Si el tempo esta bien, tienen que coincidir. Salio un
95% de mediana: los mapas eran buenos y el aviso mentia. Solo 4 de 30 bajaban
del 85%.

Con esos 4 como referencia, que estadistico los separa:

| criterio | detecta | falsas alarmas |
|---|---|---|
| residuo medio > 25 ms | 3 de 4 | 5 de 26 |
| **residuo medio > 35 ms** | **3 de 4** | **1 de 26** |
| residuo medio > 40 ms | 2 de 4 | 1 de 26 |
| beats fuera de presupuesto | no separa: 39% en los malos, 26% en los buenos |
| residuo maximo | no separa: los rangos se solapan |
| residuo MEDIANO | no separa: satura en el presupuesto |

Lo de la mediana tiene explicacion y conviene dejarla escrita: `_segment` alarga
cada tramo mientras la desviacion se mantenga bajo el presupuesto, asi que sobre
cualquier grabacion que el segmentador consiga modelar la mayoria de los beats
quedan por debajo. En las 30 canciones medidas la mediana valia 10 ms o menos en
TODAS, buenas y malas. **La informacion esta en la cola**, en los beats que el
segmentador no consiguio explicar, y eso lo recoge la media y no la mediana.

Se expresa como multiplo del presupuesto y no como 35 ms fijos para que siga
teniendo sentido si alguien cambia `--max-error`.

Blind spot conocido: de los 4 malos se escapa uno cuyo mapa encaja muy bien con
los beats detectados pero no con el chart humano. Eso pasa cuando el detector se
engancha al doble o a la mitad del pulso, y ningun residuo puede verlo: el ajuste
es coherente consigo mismo. Lo detecta `align` comparando con el BPM de la
tablatura, pero sin tablatura no hay con que comparar."""


@dataclass
class TempoFit:
    tempo_map: TempoMap
    origin_tick: int
    """Tick del primer downbeat. La rejilla musical arranca aqui."""
    beat_ticks: list[int]
    beats_per_measure: int
    residuals: list[float]
    """Desviacion por beat, en segundos, entre el mapa ajustado y lo detectado."""
    budget: float
    """Umbral con el que se ajusto, para interpretar los residuos."""

    @property
    def max_residual(self) -> float:
        return max(self.residuals)

    @property
    def mean_residual(self) -> float:
        return sum(self.residuals) / len(self.residuals)

    @property
    def median_residual(self) -> float:
        """Mas representativo que la media: un pico aislado en un cambio de
        tempo no dice que el ajuste sea malo, y a la media si la mueve."""
        ordered = sorted(self.residuals)
        return ordered[len(ordered) // 2]

    @property
    def beats_over_budget(self) -> int:
        return sum(1 for r in self.residuals if r > self.budget)

    @property
    def is_reliable(self) -> bool:
        """Un pico aislado en un cambio de tempo es normal: el modelo situa el
        cambio en un beat y la transicion real cae entre dos. Lo que delata un
        ajuste malo es que el error se salga del presupuesto EN CONJUNTO.

        Ver `RELIABLE_RESIDUAL_FACTOR` para de donde sale el umbral y por que no
        se mira ni la mediana ni el recuento de beats fuera de presupuesto."""
        return self.mean_residual <= RELIABLE_RESIDUAL_FACTOR * self.budget

    @property
    def tempo_events(self) -> int:
        return len(self.tempo_map.tempos)

    def describe(self) -> str:
        return (
            f"compas {self.beats_per_measure}/4 | "
            f"{len(self.beat_ticks)} beats | "
            f"{self.tempo_events} eventos de tempo | "
            f"residuo medio {self.mean_residual * 1000:.1f} ms "
            f"(max {self.max_residual * 1000:.1f} ms, "
            f"{self.beats_over_budget} beats fuera de presupuesto)"
        )


def _segment(times: list[float], max_error: float,
             min_beats: int) -> list[tuple[int, float]]:
    """Parte la lista de beats en tramos de tempo constante.

    Devuelve [(indice_inicial, bpm)]. El BPM de cada tramo es el promedio exacto
    sobre el tramo, de forma que sus extremos caen sobre los beats detectados y
    el error no se propaga de un tramo al siguiente.
    """
    import numpy as np

    t = np.asarray(times, dtype=float)
    n = len(t)
    segments: list[tuple[int, float]] = []
    i = 0

    while i < n - 1:
        best_end = min(i + min_beats, n - 1)
        best_bpm = quantize_bpm((best_end - i) * 60.0 / (t[best_end] - t[i]))

        j = best_end + 1
        while j <= n - 1:
            bpm = quantize_bpm((j - i) * 60.0 / (t[j] - t[i]))
            k = np.arange(i, j + 1)
            predicted = t[i] + (k - i) * (60.0 / bpm)
            if np.abs(predicted - t[i:j + 1]).max() > max_error:
                break
            best_end, best_bpm = j, bpm
            j += 1

        segments.append((i, best_bpm))
        i = best_end

    if not segments:  # menos beats que min_beats
        segments = [(0, quantize_bpm((n - 1) * 60.0 / (times[-1] - times[0])))]
    return segments


def fit(
    beats: Beats,
    pad_seconds: float = PAD_SECONDS,
    resolution: int = RESOLUTION,
    max_error: float = DEFAULT_MAX_ERROR,
    min_segment_beats: int = MIN_SEGMENT_BEATS,
) -> TempoFit:
    """Construye un TempoMap alineado a los beats detectados.

    `beats` se asume medido sobre el audio ORIGINAL; se desplaza por
    `pad_seconds` para pasar al audio final ya padeado.
    """
    shifted = beats.shifted(pad_seconds)
    bpm_meter = shifted.beats_per_measure()

    # La rejilla musical arranca en el primer downbeat: los beats anteriores
    # serian una anacrusa y no tienen compas al que pertenecer.
    origin_time = (shifted.downbeat_times[0] if shifted.downbeat_times
                   else shifted.beat_times[0])
    grid = [t for t in shifted.beat_times if t >= origin_time - 1e-9]
    if len(grid) < 2:
        raise ValueError("No quedan beats suficientes despues del primer downbeat.")

    segments = _segment(grid, max_error, min_segment_beats)
    song_bpm = segments[0][1]

    # Lead-in: se elige un numero entero de compases para que el primer downbeat
    # caiga en un limite de compas, como haria un charter a mano. Redondear al
    # compas mas cercano mantiene el BPM del lead-in proximo al de la cancion en
    # lugar de generar un absurdo de 24 BPM.
    ticks_per_measure = resolution * bpm_meter
    seconds_per_measure = bpm_meter * 60.0 / song_bpm
    n_measures = max(1, round(origin_time / seconds_per_measure))
    origin_tick = n_measures * ticks_per_measure
    lead_bpm = quantize_bpm((origin_tick / resolution) * 60.0 / origin_time)

    tempos = [TempoEvent(0, lead_bpm)]
    for index, bpm in segments:
        tick = origin_tick + index * resolution
        if tempos and abs(tempos[-1].bpm - bpm) < 1e-9:
            continue  # mismo tempo que el tramo anterior: no aporta nada
        tempos.append(TempoEvent(tick, bpm))

    tempo_map = TempoMap(tempos, [TimeSignature(0, bpm_meter, 4)], resolution)

    beat_ticks = [origin_tick + i * resolution for i in range(len(grid))]
    residuals = [abs(tempo_map.seconds_at_tick(tick) - t)
                 for tick, t in zip(beat_ticks, grid)]

    return TempoFit(
        tempo_map=tempo_map,
        origin_tick=origin_tick,
        beat_ticks=beat_ticks,
        beats_per_measure=bpm_meter,
        residuals=residuals,
        budget=max_error,
    )


def best_fit(candidates: dict[str, Beats], **kwargs) -> tuple[str, Beats, TempoFit]:
    """Prueba varios detectores y se queda con el que mejor se deja modelar.

    No hay un detector que gane siempre, y elegir a ciegas sale caro. Medido
    sobre 24K Magic (produccion moderna, tempo programado):

    | detector | eventos | residuo medio | beats fuera de presupuesto |
    |---|---|---|---|
    | PLP | 104 | 62.4 ms | 236 de 432 |
    | beat_track | 69 | 6.6 ms | 62 de 404 |

    Y sobre percusion sintetica que pasa de 128 a 96 BPM ocurre lo contrario:
    beat_track aplana todo a 96 y se come la primera seccion, mientras que PLP
    sigue los dos tempos. La flexibilidad local ayuda cuando el tempo se mueve y
    estorba cuando no.

    El criterio es la fraccion de beats que el ajuste no consigue explicar dentro
    del presupuesto, no el residuo a secas: un detector que encuentre la mitad de
    los beats tendria un residuo bajo sin ser mejor.
    """
    if not candidates:
        raise ValueError("No hay candidatos que comparar.")

    scored = []
    for name, beats in candidates.items():
        try:
            fitted = fit(beats, **kwargs)
        except (ValueError, ZeroDivisionError):
            continue
        share = fitted.beats_over_budget / max(1, len(fitted.residuals))
        scored.append((share, fitted.mean_residual, name, beats, fitted))

    if not scored:
        raise ValueError("Ningun detector produjo un mapa de tempo utilizable.")

    scored.sort(key=lambda item: (item[0], item[1]))
    _, _, name, beats, fitted = scored[0]
    return name, beats, fitted
