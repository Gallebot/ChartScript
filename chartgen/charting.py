"""Decisiones de charting compartidas por todos los frontends.

Reducir alturas a cinco botones, recortar acordes, decidir sustains, HOPOs y
frases de Star Power no depende de si las notas vinieron de una tablatura de
Guitar Pro o de transcribir el audio: es diseño de chart. Vive aqui para que un
frontend nuevo no tenga que importar del de al lado ni reimplementarlo peor.

Todo lo de este modulo trabaja sobre `RawNote`, que es una nota ya en ticks pero
todavia con su ALTURA MUSICAL: la reduccion a carriles no se ha hecho aun. Cada
frontend se encarga de llegar hasta ahi y el resto es comun.

Los numeros estan calibrados sobre 192 charts humanos, no elegidos a ojo; cada
constante lleva la medicion que la respalda.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import RESOLUTION
from .ir import Note, Phrase, Track
from .tempo import TempoMap

MIN_SUSTAIN = RESOLUTION // 2
"""Corchea. Medido sobre 192 charts reales: el 17% de las notas de guitarra y el
30% de las de bajo llevan sustain, y la mayoria son cortos. Con el umbral en una
negra se quedaban en el 8%."""

MAX_CHORD = 3
"""Nunca se generan acordes de 4 o mas notas.

No es una preferencia: en 116.771 eventos de guitarra de 192 charts reales hay
UN solo acorde de cuatro notas y ninguno de cinco. Los humanos no los escriben."""

CHORD_SPREAD = ((5, 1), (9, 2))
"""Intervalo en semitonos -> separacion de carriles. Por encima, separacion 3.

Calibrado contra la distribucion real de acordes de dos notas: 45% adyacentes,
43% con un hueco, 12% con dos. Con el corte en 4 semitonos las cuartas justas
(5) se iban a separacion 2 y la distribucion salia desplazada hacia lo ancho;
subirlo a 5 mete terceras y cuartas en carriles contiguos, que es donde estan."""

SUSTAIN_GAP = RESOLUTION // 8
"""Hueco que se deja antes de la nota siguiente para que no se peguen."""

HOPO_THRESHOLD = 65
"""Umbral de HOPO automatico del juego: 1/12 de negra con RESOLUTION=192."""

PHRASE_GAP = RESOLUTION * 2
"""Silencio que separa dos frases. Media redonda a la resolucion estandar."""

MAX_PHRASE = RESOLUTION * 8
"""Dos compases de 4/4. Se reancla aunque no haya silencios.

Sin este tope, un riff continuo sin pausas convierte la cancion entera en una
sola frase: sus alturas distintas se comprimen a cinco carriles, notas vecinas
colisionan en el mismo boton y se pierden HOPOs que la tablatura si marcaba."""


@dataclass
class RawNote:
    """Nota leida de la tablatura, antes de decidir su carril."""

    tick: int
    pitch: int
    """Altura de la nota mas grave: es la que guia el contorno."""
    length: int
    hammer: bool = False
    slide: bool = False
    pitches: tuple[int, ...] = ()
    """Todas las alturas del acorde. Vacio equivale a monofonico."""

    @property
    def legato(self) -> bool:
        return self.hammer or self.slide

    @property
    def is_chord(self) -> bool:
        return len(self.pitches) > 1


# --------------------------------------------------------------------------
# Reduccion a cinco carriles
# --------------------------------------------------------------------------

def split_phrases(notes: list[RawNote], gap: int = PHRASE_GAP,
                  max_length: int = MAX_PHRASE) -> list[list[RawNote]]:
    """Parte en frases por silencios. Cada frase se mapea de forma independiente.

    Reanclar en cada frase evita la deriva: con un mapeo puramente relativo, una
    linea que sube mucho acaba pegada al naranja y se queda ahi el resto del tema.
    """
    if not notes:
        return []
    phrases, current = [], [notes[0]]
    for previous, note in zip(notes, notes[1:]):
        silence = note.tick - (previous.tick + previous.length) >= gap
        too_long = note.tick - current[0].tick >= max_length
        if silence or too_long:
            phrases.append(current)
            current = [note]
        else:
            current.append(note)
    phrases.append(current)
    return phrases


def map_lanes(notes: list[RawNote], lanes: int = 5, gap: int = PHRASE_GAP,
              max_phrase: int = MAX_PHRASE) -> list[int]:
    """Asigna un carril a cada nota. Este es el corazon de la conversion.

    La estrategia es ordenar las alturas DISTINTAS de cada frase y repartirlas
    en carriles contiguos, desplazando el bloque segun el registro de la frase
    dentro del rango de la cancion.

    Se descarto el mapeo por intervalos relativos (cada salto de tono mueve N
    carriles): acumula deriva y termina anclado en un extremo. Y tambien el
    mapeo por altura absoluta, que rompe el contorno cuando la linea se mueve
    poco pero cruza una frontera de carril.

    Lo que se conserva aqui es el CONTORNO, que es lo que hace que un chart se
    sienta bien: si la musica sube, los botones suben. Un riff de tres notas usa
    tres botones contiguos, y siempre los mismos, este donde este en la cancion.
    """
    if not notes:
        return []

    pitches = [n.pitch for n in notes]
    low, high = min(pitches), max(pitches)
    span = max(1, high - low)

    result: dict[int, int] = {}
    for phrase in split_phrases(notes, gap, max_phrase):
        distinct = sorted({n.pitch for n in phrase})

        if len(distinct) <= lanes:
            # Cuantos carriles sobran a los lados, y donde colocar el bloque.
            slack = lanes - len(distinct)
            centre = sum(distinct) / len(distinct)
            position = (centre - low) / span
            base = round(position * slack)
            assignment = {p: base + i for i, p in enumerate(distinct)}
        else:
            # Mas alturas que botones: se reparten por cuantiles conservando el
            # orden. Se pierden repeticiones, no el contorno.
            assignment = {
                p: min(lanes - 1, i * lanes // len(distinct))
                for i, p in enumerate(distinct)
            }

        for note in phrase:
            result[id(note)] = max(0, min(lanes - 1, assignment[note.pitch]))

    return [result[id(n)] for n in notes]


# --------------------------------------------------------------------------
# Acordes (M3b)
# --------------------------------------------------------------------------

def collapse_octaves(pitches: tuple[int, ...]) -> list[int]:
    """Descarta duplicados de octava: son la misma nota, no una voz mas.

    Importa mucho mas de lo que parece. Un power chord se digita en tres cuerdas
    (fundamental, quinta y la fundamental una octava arriba) pero solo tiene DOS
    alturas distintas, y un charter humano lo escribe con dos botones. Sin este
    paso se generaba un 38% de acordes de tres notas cuando en los charts reales
    son el 9%.
    """
    kept: list[int] = []
    for pitch in sorted(set(pitches)):
        if not any((pitch - other) % 12 == 0 for other in kept):
            kept.append(pitch)
    return kept


def reduce_chord(pitches: tuple[int, ...], max_notes: int = MAX_CHORD) -> list[int]:
    """Recorta un acorde de la tablatura a como mucho `max_notes` alturas.

    Se conservan los extremos, que son los que definen el caracter del acorde:
    la fundamental abajo y la voz superior arriba. Lo de en medio es relleno
    armonico que en cinco botones no cabe ni se nota.
    """
    unique = collapse_octaves(pitches)
    if len(unique) <= max_notes:
        return unique
    if max_notes == 1:
        return [unique[0]]
    if max_notes == 2:
        return [unique[0], unique[-1]]
    middle = unique[len(unique) // 2]
    return sorted({unique[0], middle, unique[-1]})


def chord_spread(interval: int) -> int:
    """Distancia entre carriles para un intervalo dado, en semitonos."""
    for limit, spread in CHORD_SPREAD:
        if interval <= limit:
            return spread
    return 3


def chord_lanes(root_lane: int, pitches: list[int], lanes: int = 5) -> list[int]:
    """Coloca un acorde en los carriles a partir de la nota mas grave.

    El carril de la fundamental sale del mapeo de contorno, igual que una nota
    suelta, asi que el acorde queda en el registro correcto. Las voces de encima
    se separan segun el intervalo musical.
    """
    if len(pitches) <= 1:
        return [max(0, min(lanes - 1, root_lane))]

    offsets = [0]
    for pitch in pitches[1:]:
        step = chord_spread(pitch - pitches[0])
        offsets.append(max(offsets[-1] + 1, step))

    # Si el acorde se sale por arriba, se baja entero: nunca se comprime, porque
    # comprimir juntaria dos voces en el mismo boton y perderia una nota.
    width = offsets[-1]
    base = max(0, min(root_lane, lanes - 1 - width))
    return [base + offset for offset in offsets]


def build_notes(raw: list[RawNote], root_lanes: list[int],
                min_sustain: int = MIN_SUSTAIN,
                max_chord: int = MAX_CHORD) -> tuple[list[Note], list[list[int]]]:
    """Construye las notas finales, expandiendo acordes. Devuelve (notas, carriles)."""
    notes: list[Note] = []
    per_beat: list[list[int]] = []

    for index, (item, root_lane) in enumerate(zip(raw, root_lanes)):
        length = item.length
        if index + 1 < len(raw):
            available = raw[index + 1].tick - item.tick - SUSTAIN_GAP
            length = min(length, max(0, available))
        sustain = length if length >= min_sustain else 0

        pitches = reduce_chord(item.pitches, max_chord) if item.is_chord else []
        lanes = chord_lanes(root_lane, pitches) if pitches else [root_lane]
        lanes = sorted({max(0, min(4, lane)) for lane in lanes})

        per_beat.append(lanes)
        for lane in lanes:
            notes.append(Note(tick=item.tick, fret=lane, sustain=sustain))

    return notes, per_beat


def apply_hopos_polyphonic(per_beat: list[list[int]], raw: list[RawNote],
                           notes: list[Note], cancel_natural: bool = False) -> None:
    """Ajusta `force` por beat, no por nota.

    El flag `N 5` no ACTIVA el HOPO: lo INVIERTE. El juego ya decide solo que una
    nota a menos de 65 ticks de la anterior y en otro carril es HOPO, asi que
    solo hay algo que escribir donde la tablatura y el automatismo discrepan.

    Hay dos discrepancias posibles y los humanos las tratan MUY distinto. Medido
    sobre 192 charts reales:

    | | HOPO natural cancelado | HOPO forzado desde lejos |
    |---|---|---|
    | guitarra | 12.9% | 1.4% |
    | bajo | 48.4% | 0.5% |

    Es decir: cuando la tablatura marca un ligado que el juego no haria solo, se
    fuerza (poco frecuente pero claro). Cuando el juego crea un HOPO que la
    tablatura no pedia, los charters lo DEJAN VIVIR la mayoria de las veces.

    Por eso `cancel_natural` es False por defecto. Cancelarlos todos, que era el
    comportamiento anterior, marcaba el 46% de las notas de una linea de bajo
    funk con semicorcheas: mas dificil de tocar y muy lejos del 8% real.
    Un acorde nunca es HOPO natural, asi que nunca necesita flag.
    """
    by_tick: dict[int, list[Note]] = {}
    for note in notes:
        by_tick.setdefault(note.tick, []).append(note)

    for index in range(1, len(raw)):
        previous_lanes, current_lanes = per_beat[index - 1], per_beat[index]
        close = (raw[index].tick - raw[index - 1].tick) <= HOPO_THRESHOLD
        changed = current_lanes != previous_lanes
        single = len(current_lanes) == 1 and len(previous_lanes) == 1

        natural = close and changed and len(current_lanes) == 1
        desired = raw[index].legato and changed and single

        if desired and not natural:
            flag = True          # la tab pide ligado y el juego no lo daria
        elif natural and not desired:
            flag = cancel_natural
        else:
            flag = False

        if flag:
            for note in by_tick.get(raw[index].tick, []):
                note.force = True



# --------------------------------------------------------------------------
# Star Power
# --------------------------------------------------------------------------

def add_star_power(track: Track, tempo_map: TempoMap, origin: int,
                   every: int = 8, length_measures: int = 2) -> None:
    """Frases de Star Power en limites de compas.

    No "cada N notas": las frases tienen que caer en el compas o el chart se
    siente arbitrario al jugarlo.
    """
    if not track.notes:
        return
    last = max(n.tick + n.sustain for n in track.notes)
    ticks_per_measure = tempo_map.ticks_per_measure_at(origin)
    span = ticks_per_measure * length_measures

    start = origin + ticks_per_measure * every
    while start + span <= last:
        if any(start <= n.tick < start + span for n in track.notes):
            track.star_power.append(Phrase(tick=start, length=span))
        start += ticks_per_measure * every
