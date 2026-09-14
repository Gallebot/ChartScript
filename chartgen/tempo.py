"""Conversion segundos <-> ticks a traves del mapa de tempo.

Esta es la pieza central de todo el pipeline: cualquier frontend produce tiempos
en segundos y cualquier backend necesita ticks. Todo pasa por aqui.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from . import RESOLUTION


def quantize_bpm(bpm: float) -> float:
    """Redondea un BPM a lo que el .chart puede representar (milesimas).

    El evento `B` guarda BPM * 1000 como entero, asi que 174.267 se escribe como
    `B 174267`. Si el mapa interno guardara 174.2673 nuestros ticks derivarian
    lentamente de los que calcula el juego. Cuantizamos al construir para que el
    mapa en memoria y el archivo en disco sean la misma linea de tiempo.
    """
    if bpm <= 0:
        raise ValueError(f"BPM debe ser positivo, se recibio {bpm}")
    return round(bpm * 1000) / 1000.0


@dataclass(frozen=True)
class TempoEvent:
    tick: int
    bpm: float


@dataclass(frozen=True)
class TimeSignature:
    tick: int
    numerator: int
    denominator: int = 4

    def __post_init__(self) -> None:
        d = self.denominator
        if d < 1 or (d & (d - 1)) != 0:
            raise ValueError(
                f"El denominador de compas debe ser potencia de 2, se recibio {d}"
            )

    @property
    def denominator_exponent(self) -> int:
        """El .chart guarda el denominador como log2: `TS 6 3` es 6/8."""
        return self.denominator.bit_length() - 1


class TempoMap:
    """Mapa de tempo con busqueda O(log n) en ambos sentidos."""

    def __init__(
        self,
        tempos: list[TempoEvent] | None = None,
        time_signatures: list[TimeSignature] | None = None,
        resolution: int = RESOLUTION,
    ) -> None:
        self.resolution = resolution
        tempos = list(tempos) if tempos else [TempoEvent(0, 120.0)]
        tempos = [TempoEvent(t.tick, quantize_bpm(t.bpm)) for t in tempos]
        tempos.sort(key=lambda t: t.tick)
        if tempos[0].tick != 0:
            raise ValueError("El mapa de tempo debe empezar con un evento en el tick 0")

        # Colapsa duplicados en el mismo tick (gana el ultimo) y descarta los
        # que repiten el BPM anterior: un frontend que emite un evento por
        # compas llenaria el SyncTrack de ruido inutil para editar a mano.
        deduped: list[TempoEvent] = []
        for ev in tempos:
            if deduped and deduped[-1].tick == ev.tick:
                deduped[-1] = ev
            elif deduped and deduped[-1].bpm == ev.bpm:
                continue
            else:
                deduped.append(ev)
        self.tempos = deduped

        ts = list(time_signatures) if time_signatures else [TimeSignature(0, 4, 4)]
        ts.sort(key=lambda s: s.tick)
        if ts[0].tick != 0:
            raise ValueError("El mapa de compases debe empezar con un evento en el tick 0")
        compact: list[TimeSignature] = []
        for sig in ts:
            if compact and compact[-1].tick == sig.tick:
                compact[-1] = sig
            elif compact and (compact[-1].numerator, compact[-1].denominator) == (
                    sig.numerator, sig.denominator):
                continue
            else:
                compact.append(sig)
        self.time_signatures = compact

        self._ticks = [t.tick for t in self.tempos]
        self._seconds = self._build_cumulative()

    def _build_cumulative(self) -> list[float]:
        """Segundos absolutos en los que ocurre cada cambio de tempo."""
        out = [0.0]
        for prev, nxt in zip(self.tempos, self.tempos[1:]):
            span = nxt.tick - prev.tick
            out.append(out[-1] + span * self._sec_per_tick(prev.bpm))
        return out

    def _sec_per_tick(self, bpm: float) -> float:
        return 60.0 / (bpm * self.resolution)

    def seconds_at_tick(self, tick: float) -> float:
        if tick < 0:
            raise ValueError(f"Tick negativo: {tick}")
        i = max(0, bisect_right(self._ticks, tick) - 1)
        base = self.tempos[i]
        return self._seconds[i] + (tick - base.tick) * self._sec_per_tick(base.bpm)

    def tick_at_seconds(self, seconds: float) -> int:
        """Inverso de seconds_at_tick. Redondea al tick mas cercano."""
        if seconds < 0:
            raise ValueError(f"Tiempo negativo: {seconds}")
        i = max(0, bisect_right(self._seconds, seconds) - 1)
        base = self.tempos[i]
        delta = seconds - self._seconds[i]
        return round(base.tick + delta / self._sec_per_tick(base.bpm))

    def bpm_at_tick(self, tick: float) -> float:
        i = max(0, bisect_right(self._ticks, tick) - 1)
        return self.tempos[i].bpm

    def time_signature_at_tick(self, tick: float) -> TimeSignature:
        best = self.time_signatures[0]
        for ts in self.time_signatures:
            if ts.tick > tick:
                break
            best = ts
        return best

    def ticks_per_measure_at(self, tick: float) -> int:
        ts = self.time_signature_at_tick(tick)
        return int(self.resolution * 4 * ts.numerator / ts.denominator)

    @classmethod
    def constant(cls, bpm: float, resolution: int = RESOLUTION) -> TempoMap:
        return cls([TempoEvent(0, bpm)], None, resolution)

    def __repr__(self) -> str:
        return (
            f"TempoMap(res={self.resolution}, tempos={len(self.tempos)}, "
            f"ts={len(self.time_signatures)}, first={self.tempos[0].bpm}bpm)"
        )
