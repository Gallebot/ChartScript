"""Mapeo de percusion General MIDI a los carriles de bateria de Clone Hero.

## El formato de destino

Medido sobre los 78 charts con bateria de la coleccion de referencia:

| valor | significado | apariciones |
|---|---|---|
| 0 | bombo | 21.381 |
| 1 | rojo (caja) | 19.706 |
| 2 | amarillo | 32.100 |
| 3 | azul | 7.826 |
| 4 | verde | 3.254 |
| 66 / 67 / 68 | marcador de platillo para 2 / 3 / 4 | 9.840 / 2.581 / 566 |

Y dos datos que simplifican mucho la conversion frente a la de guitarra:
los charts de bateria no llevan **ningun** sustain, y en toda la coleccion hay
**un solo** flag de force. Aqui no hay HOPO ni notas mantenidas que calcular.

Golpes simultaneos: 35.277 de uno, 22.694 de dos, 1.201 de tres. Cuatro a la vez
practicamente no existe.

## De donde viene la entrada

Guitar Pro marca las pistas de percusion con `isPercussionTrack` y canal 9, y
ahi `note.realValue` es directamente el numero de percusion General MIDI. No hay
que deducir nada: es una tabla de equivalencias.

La parte que si tiene criterio es que un kit de bateria tiene mas piezas que
carriles. Toms agudos y charles comparten el amarillo, ride y toms medios el
azul, crash y toms graves el verde. Es la reduccion estandar de Rock Band y la
que usan los charts de la coleccion.
"""

from __future__ import annotations

from dataclasses import dataclass

KICK, RED, YELLOW, BLUE, GREEN = 0, 1, 2, 3, 4

CYMBAL_MARKER = {YELLOW: 66, BLUE: 67, GREEN: 68}
"""Marca un carril como platillo en vez de tom (Pro Drums)."""


@dataclass(frozen=True)
class Pad:
    lane: int
    cymbal: bool = False


# --------------------------------------------------------------------------
# Tabla General MIDI -> carril
# --------------------------------------------------------------------------

_KIT = {
    # Bombo
    35: Pad(KICK), 36: Pad(KICK),
    # Caja y equivalentes
    37: Pad(RED),        # golpe en el aro
    38: Pad(RED), 40: Pad(RED),
    39: Pad(RED),        # palmas: van con la caja, es donde caen ritmicamente
    # Charles -> amarillo, siempre platillo
    42: Pad(YELLOW, True), 44: Pad(YELLOW, True), 46: Pad(YELLOW, True),
    # Toms: agudo amarillo, medio azul, grave verde
    48: Pad(YELLOW), 50: Pad(YELLOW),
    45: Pad(BLUE), 47: Pad(BLUE),
    41: Pad(GREEN), 43: Pad(GREEN),
    # Ride -> azul, platillo
    51: Pad(BLUE, True), 53: Pad(BLUE, True), 59: Pad(BLUE, True),
    # Crash, china, splash -> verde, platillo
    49: Pad(GREEN, True), 52: Pad(GREEN, True), 55: Pad(GREEN, True),
    57: Pad(GREEN, True),
}

_LATIN = {
    # Percusion de mano y agudos: suenan como el charles, van al amarillo
    54: Pad(YELLOW, True),   # pandereta
    56: Pad(YELLOW, True),   # cencerro
    69: Pad(YELLOW, True),   # cabasa
    70: Pad(YELLOW, True),   # maracas
    82: Pad(YELLOW, True),   # shaker
    75: Pad(YELLOW, True),   # claves
    76: Pad(YELLOW), 77: Pad(YELLOW),   # bloques de madera
    # Bongos: agudos, al amarillo y azul
    60: Pad(YELLOW), 61: Pad(BLUE),
    # Congas: cuerpo medio y grave
    62: Pad(BLUE), 63: Pad(BLUE), 64: Pad(GREEN),
    # Timbales y agogo
    65: Pad(BLUE), 66: Pad(GREEN), 67: Pad(YELLOW), 68: Pad(YELLOW),
    # Guiro, cuica, triangulo
    73: Pad(YELLOW), 74: Pad(YELLOW),
    78: Pad(YELLOW), 79: Pad(BLUE),
    80: Pad(YELLOW, True), 81: Pad(YELLOW, True),
}

MAPPING: dict[int, Pad] = {**_LATIN, **_KIT}
"""El kit manda sobre la percusion latina donde los numeros se solapan."""

DEFAULT = Pad(YELLOW)
"""Cualquier pieza desconocida cae en el amarillo, que es el carril de relleno."""


def to_pad(midi_note: int) -> Pad:
    """Traduce un numero de percusion General MIDI a carril."""
    return MAPPING.get(int(midi_note), DEFAULT)


def has_kit(notes) -> bool:
    """True si el material parece una bateria y no percusion suelta.

    'And I Love Her' de los Beatles trae cabasa, shaker, congas y claves, sin
    bombo ni caja. Se puede chartear igual, pero conviene decirlo: el resultado
    no se va a parecer a tocar una bateria.
    """
    values = {int(n) for n in notes}
    return bool(values & {35, 36}) and bool(values & {38, 40})


def describe(notes) -> str:
    """Resumen legible de que piezas trae el material."""
    from collections import Counter

    counts = Counter(to_pad(n).lane for n in notes)
    names = {KICK: "bombo", RED: "caja", YELLOW: "amarillo", BLUE: "azul",
             GREEN: "verde"}
    return ", ".join(f"{names[lane]} {counts[lane]}"
                     for lane in sorted(counts))
