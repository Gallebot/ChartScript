"""Combinaciones de asignacion pista-del-MIDI -> canal-del-chart.

## El problema que resuelve

Un MIDI de transcripcion trae pistas con nombres de su propio vocabulario
(`voice`, `electric_bass`, `synth_pad`, `violin`) y no hay una respuesta unica a
cual de ellas deberia ser el canal de guitarra. La voz suele llevar la melodia y
se juega bien; el bajo es mas fiel pero mas plano. Probarlo es mas rapido que
razonarlo.

Asi que en vez de elegir por el usuario, se generan varias carpetas y se juegan.

## El modelo

Para cada canal del chart, el usuario da una LISTA de candidatos. Una variante es
un elemento de cada lista:

    guitar: [voice, electric_bass]
    bass:   [electric_bass]
    drums:  [drums]

    -> variante 1: guitar=voice,          bass=electric_bass, drums=drums
    -> variante 2: guitar=electric_bass,  bass=electric_bass, drums=drums

La segunda repite `electric_bass` en dos canales. Por defecto se descarta: casi
siempre es un accidente de la combinatoria, no lo que alguien queria. Con
`allow_reuse` se conserva, porque duplicar una pista en dos canales es legitimo
si lo que se busca es un chart de dos jugadores tocando lo mismo.

## Por que hay un tope

El producto crece rapido: cinco canales con tres candidatos cada uno son 243
carpetas, y cada una lleva su copia del audio. `expand` no decide nada al
respecto: devuelve todo y quien llama decide cuanto genera, con `MAX_VARIANTS`
como tope por defecto.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

EMPTY = None
"""Un canal puede quedarse vacio: es un candidato mas, no una excepcion."""

SHORT = {
    "guitar": "gtr",
    "bass": "bass",
    "rhythm": "rhy",
    "coop": "coop",
    "keys": "keys",
    "drums": "drums",
}
"""Abreviaturas para el sufijo del titulo. `guitar` se abrevia porque es el canal
que casi siempre varia y el nombre largo se come el ancho de la lista del juego."""

MAX_VARIANTS = 24
"""Tope por defecto. No es un limite tecnico: es el punto en el que generar mas
carpetas cuesta mas que mirar las que ya hay."""


@dataclass
class Variant:
    """Una asignacion concreta, con su etiqueta.

    La direccion del diccionario importa y costo un bug: la primera version iba de
    PISTA a canal, y con eso una pista no puede alimentar dos canales. Al pedir
    `guitar=electric_bass` junto con `bass=electric_bass` y `--allow-reuse`, la
    segunda entrada pisaba la primera y el canal de guitarra desaparecia del chart
    en silencio, aunque la etiqueta de la carpeta siguiera diciendo que estaba.

    Asi que va de CANAL a lista de pistas, que es la forma del problema: un canal
    admite varias pistas sumadas, y una pista puede ir a varios canales.
    """

    assignment: dict[str, list[str]]
    """Canal del chart -> pistas del MIDI que van dentro, en orden."""

    @property
    def by_channel(self) -> dict[str, str]:
        """Canal -> pistas en texto, para etiquetar y mostrar."""
        return {channel: "+".join(tracks)
                for channel, tracks in self.assignment.items()}

    def label(self, varying: tuple[str, ...] = ()) -> str:
        """Sufijo para el titulo. Solo nombra los canales que de verdad cambian.

        Si las cinco variantes comparten `drums=drums`, ponerlo en las cinco
        etiquetas no distingue nada y solo alarga el nombre.
        """
        shown = self.by_channel
        channels = varying or tuple(shown)
        parts = [f"{SHORT.get(c, c)}={shown[c]}" for c in channels if c in shown]
        return f"[{', '.join(parts)}]" if parts else ""

    def describe(self) -> str:
        return ", ".join(f"{c}={t}" for c, t in sorted(self.by_channel.items()))


def varying_channels(choices: dict[str, list[str | None]]) -> tuple[str, ...]:
    """Los canales con mas de un candidato: los unicos que distinguen variantes."""
    return tuple(c for c, candidates in choices.items() if len(candidates) > 1)


@dataclass
class Expansion:
    """El resultado de expandir, con cuenta de lo que se quedo fuera.

    Lo descartado se cuenta y se devuelve en vez de tirarse en silencio porque el
    caso que mas confunde es justo ese: pedir `guitar=voice|electric_bass` junto
    con `bass=electric_bass` y recibir dos carpetas en vez de tres, sin que nada
    diga que la tercera se cayo por repetir una pista.
    """

    variants: list[Variant]
    skipped_reuse: int = 0
    skipped_duplicate: int = 0

    def notes(self) -> list[str]:
        """Lo que hay que contarle al usuario sobre lo descartado."""
        out = []
        if self.skipped_reuse:
            out.append(
                f"{self.skipped_reuse} combinacion(es) descartadas por usar la "
                "misma pista en dos canales. Si es lo que querias (por ejemplo "
                "la misma linea en guitarra y bajo), agrega --allow-reuse.")
        if self.skipped_duplicate:
            out.append(f"{self.skipped_duplicate} combinacion(es) eran "
                       "duplicados exactos de otra y se unificaron.")
        return out


def expand(choices: dict[str, list[str | None]],
           allow_reuse: bool = False) -> Expansion:
    """Producto cartesiano de los candidatos, sin variantes vacias ni repetidas.

    Se descartan tres cosas, en este orden:

    1. Las combinaciones que dejan TODOS los canales vacios: no hay chart.
    2. Las que usan la misma pista en dos canales, salvo `allow_reuse`.
    3. Las duplicadas, que solo salen de un candidato REPETIDO en la lista de un
       mismo canal. Por CLI y por la TUI ese contador es siempre 0 porque las dos
       deduplican al leer; queda como red por si alguien construye `choices` a
       mano. (La version anterior de este docstring lo justificaba con varios
       canales a `EMPTY`, y eso no lo alcanza nunca: con un candidato por canal,
       dos combinaciones siempre difieren en algun canal que esta o no esta.)
    """
    channels = [c for c, candidates in choices.items() if candidates]
    if not channels:
        return Expansion([])

    out: list[Variant] = []
    seen: set[tuple] = set()
    reuse = duplicate = 0
    for combo in product(*(choices[c] for c in channels)):
        by_channel = {c: track for c, track in zip(channels, combo)
                      if track is not EMPTY}
        if not by_channel:
            continue

        tracks = list(by_channel.values())
        if not allow_reuse and len(set(tracks)) != len(tracks):
            reuse += 1
            continue

        key = tuple(sorted(by_channel.items()))
        if key in seen:
            duplicate += 1
            continue
        seen.add(key)

        # Una combinacion da una sola pista por canal. El sumado de varias pistas
        # en un canal es cosa del modo manual, que pasa por `single`.
        out.append(Variant(assignment={channel: [track]
                                       for channel, track in by_channel.items()}))
    return Expansion(out, skipped_reuse=reuse, skipped_duplicate=duplicate)


def single(by_track: dict[str, str]) -> Variant:
    """Una variante a partir de una asignacion PISTA->canal escrita a mano.

    La entrada va al reves que la de `Variant` porque es la forma en la que el
    usuario lo escribe (`--assign "voice=guitar"`) y en la que la TUI lo pregunta
    (canal por canal, apuntando pistas). Aqui se invierte a canal -> pistas.

    A diferencia de `expand`, varias pistas pueden ir al mismo canal: es como se
    juntan dos teclados en `keys`, y ahi lo ha pedido una persona en vez de
    haberlo generado la combinatoria.
    """
    assignment: dict[str, list[str]] = {}
    for track, channel in by_track.items():
        assignment.setdefault(channel, []).append(track)
    return Variant(assignment=assignment)
