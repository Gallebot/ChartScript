"""Letras sincronizadas: de fuentes con tiempos a eventos del .chart.

## Lo que hace la comunidad

Medido sobre 192 charts reales: el **83% llevan letra sincronizada**, con una
mediana de 286 eventos por cancion. No es un extra opcional, es lo normal. Y
conviven tres convenciones distintas:

| estilo | canciones | ejemplo |
|---|---|---|
| silabas con guion | 74 | `Ar-` `ma-` `ge-` `ddon` |
| linea entera | 43 | `Puede\\xa0ser` (unida con espacios duros) |
| palabra a palabra | 42 | `Hola,` `what's` `happenin'?` |

Las tres se generan desde aqui con `mode`. Cual se puede usar depende de la
precision de la fuente: una letra en `.lrc` normal solo trae tiempos por verso,
asi que el modo linea es exacto y los otros dos reparten dentro del verso.

## El formato

    1488 = E "phrase_start"
    1536 = E "lyric Ar-"
    1584 = E "lyric ma-"
    1728 = E "phrase_end"

El guion final de una silaba significa "pegada a la siguiente, sin espacio". En
el modo linea se usa U+00A0 entre palabras por el mismo motivo: sin el, el juego
partiria el verso en trozos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .ir import Event
from .tempo import TempoMap

NBSP = " "
"""Espacio duro. Une las palabras de un verso para que el juego no lo parta."""

PHRASE_LEAD = 0.05
"""Segundos que `phrase_start` se adelanta a la primera silaba, como en los
charts reales (48 ticks a 120 BPM)."""

MIN_SYLLABLE = 0.08
"""Duracion minima de una silaba al repartir dentro de un verso."""

MAX_PIECE_PER_WEIGHT = 1.2
"""Segundos por unidad de peso silabico. Es un TECHO, no un objetivo.

Sin el, un verso corto seguido de un pasaje instrumental se estiraba hasta el
verso siguiente: 'Hola' delante de veinte segundos de pausa generaba una frase
de veinte segundos. El techo escala con el tamaño del trozo, asi que una silaba
suelta se limita a poco mas de un segundo y un verso entero a bastante mas."""


class LyricsError(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    start: float | None = None
    """Segundos. None cuando la fuente solo da tiempos por verso."""


@dataclass
class LyricLine:
    start: float
    text: str
    end: float | None = None
    words: list[Word] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.text = " ".join(self.text.split())
        if not self.words:
            self.words = [Word(w) for w in self.text.split()]

    @property
    def timed(self) -> bool:
        """True si la fuente trae tiempos por palabra (LRC mejorado)."""
        return any(w.start is not None for w in self.words)


# --------------------------------------------------------------------------
# Lectura de .lrc
# --------------------------------------------------------------------------

_STAMP = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_WORD_STAMP = re.compile(r"<(\d+):(\d{1,2})(?:[.:](\d{1,3}))?>")
_META = re.compile(r"^\[(ar|ti|al|by|offset|length|re|ve|tool):", re.IGNORECASE)


def _seconds(minutes: str, secs: str, fraction: str | None) -> float:
    total = int(minutes) * 60 + int(secs)
    if fraction:
        total += int(fraction) / (10 ** len(fraction))
    return total


def parse_lrc(path: Path, encoding: str | None = None) -> list[LyricLine]:
    """Lee un archivo .lrc. Soporta el formato simple y el mejorado (A2).

    En el mejorado cada palabra lleva su propia marca `<mm:ss.xx>`, asi que los
    modos de palabra y silaba salen exactos en vez de repartidos.
    """
    path = Path(path)
    raw = path.read_bytes()
    for candidate in ([encoding] if encoding else ["utf-8-sig", "utf-8", "cp1252",
                                                   "latin-1"]):
        try:
            text = raw.decode(candidate)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise LyricsError(f"No se pudo decodificar {path.name}")

    offset = 0.0
    lines: list[LyricLine] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _META.match(line):
            if line.lower().startswith("[offset:"):
                try:
                    offset = int(re.sub(r"[^\d-]", "", line)) / 1000.0
                except ValueError:
                    pass
            continue

        stamps = list(_STAMP.finditer(line))
        if not stamps:
            continue
        body = line[stamps[-1].end():]

        words: list[Word] = []
        if _WORD_STAMP.search(body):
            pieces = _WORD_STAMP.split(body)
            # split devuelve [previo, m, s, frac, texto, m, s, frac, texto, ...]
            leading = pieces[0].strip()
            if leading:
                words.append(Word(leading))
            for i in range(1, len(pieces), 4):
                when = _seconds(pieces[i], pieces[i + 1], pieces[i + 2])
                chunk = pieces[i + 3].strip()
                if chunk:
                    words.append(Word(chunk, when))
            body = _WORD_STAMP.sub(" ", body)

        body = " ".join(body.split())
        if not body:
            continue
        for stamp in stamps:
            when = _seconds(stamp.group(1), stamp.group(2), stamp.group(3))
            lines.append(LyricLine(start=when + offset, text=body,
                                   words=[Word(w.text, w.start) for w in words]))

    lines.sort(key=lambda item: item.start)
    for current, following in zip(lines, lines[1:]):
        if current.end is None:
            current.end = following.start
    if lines and lines[-1].end is None:
        lines[-1].end = lines[-1].start + 3.0
    return lines


# --------------------------------------------------------------------------
# Silabas
# --------------------------------------------------------------------------

def syllabify(word: str, lang: str = "es") -> list[str]:
    """Parte una palabra en silabas conservando la puntuacion.

    pyphen aplica reglas TIPOGRAFICAS de division, no fonetica. En español
    coincide casi siempre ('ca-lle-je-ra'), en ingles es mas burdo:
    'Armaged-don' donde un charter escribiria 'Ar-ma-ge-ddon'.
    """
    try:
        import pyphen
    except ImportError as exc:
        raise LyricsError(
            "El modo silaba necesita pyphen:  pip install pyphen"
        ) from exc

    prefix = ""
    suffix = ""
    core = word
    while core and not core[0].isalnum():
        prefix, core = prefix + core[0], core[1:]
    while core and not core[-1].isalnum():
        core, suffix = core[:-1], core[-1] + suffix
    if len(core) < 4:
        return [word]

    try:
        parts = pyphen.Pyphen(lang=lang).inserted(core).split("-")
    except KeyError:
        return [word]
    parts = [p for p in parts if p]
    if not parts:
        return [word]

    parts[0] = prefix + parts[0]
    parts[-1] = parts[-1] + suffix
    return parts


def _weights(chunks: list[str], lang: str) -> list[float]:
    """Reparte la duracion de un verso segun el peso silabico de cada trozo."""
    weights = []
    for chunk in chunks:
        letters = sum(1 for c in chunk if c.isalpha())
        weights.append(max(1.0, letters / 2.0))
    return weights


# --------------------------------------------------------------------------
# Generacion de eventos
# --------------------------------------------------------------------------

def _pieces(line: LyricLine, mode: str, lang: str) -> list[tuple[str, float | None]]:
    """Trocea un verso segun el modo. Devuelve (texto, segundos o None)."""
    if mode == "line":
        return [(NBSP.join(line.text.split()), line.start)]

    if mode == "word":
        return [(w.text, w.start) for w in line.words]

    if mode == "syllable":
        out: list[tuple[str, float | None]] = []
        for word in line.words:
            parts = syllabify(word.text, lang)
            for index, part in enumerate(parts):
                # El guion final indica "pegada a la siguiente, sin espacio".
                text = part + "-" if index < len(parts) - 1 else part
                out.append((text, word.start if index == 0 else None))
        return out

    raise LyricsError(f"Modo desconocido: {mode}")


def _distribute(pieces: list[tuple[str, float | None]], line: LyricLine,
                lang: str) -> tuple[list[tuple[str, float]], float]:
    """Asigna tiempo a los trozos que no lo traen.

    Los que si lo traen (LRC mejorado) se respetan tal cual; los huecos se
    rellenan repartiendo proporcionalmente al peso silabico, que se aproxima
    mejor al canto que un reparto uniforme.
    """
    end = line.end if line.end is not None else line.start + 3.0
    span = max(0.3, end - line.start)
    texts = [p[0] for p in pieces]
    weights = _weights(texts, lang)
    total = sum(weights) or 1.0

    times: list[float] = []
    cursor = line.start
    for (text, when), weight in zip(pieces, weights):
        if when is not None:
            cursor = when
        times.append(cursor)
        share = min(span * weight / total, MAX_PIECE_PER_WEIGHT * weight)
        cursor += max(MIN_SYLLABLE, share)
    # `cursor` queda donde termina de sonar la ultima silaba: es ahi donde va el
    # phrase_end, no en `line.end`, que es el arranque del verso siguiente.
    return list(zip(texts, times)), cursor


def to_events(lines: list[LyricLine], tempo_map: TempoMap,
              pad_seconds: float = 0.0, mode: str = "line",
              lang: str = "es", phrase_end: bool = True) -> list[Event]:
    """Convierte versos con tiempos en eventos de `[Events]`."""
    if mode not in ("line", "word", "syllable"):
        raise LyricsError(f"Modo desconocido: {mode}")

    events: list[Event] = []
    for index, line in enumerate(lines):
        pieces = _pieces(line, mode, lang)
        pieces = [(t, w) for t, w in pieces if t.strip()]
        if not pieces:
            continue
        placed, sung_end = _distribute(pieces, line, lang)

        first = placed[0][1]
        start_tick = tempo_map.tick_at_seconds(
            max(0.0, first + pad_seconds - PHRASE_LEAD))
        events.append(Event(start_tick, "phrase_start"))

        last_tick = start_tick
        for text, when in placed:
            tick = tempo_map.tick_at_seconds(max(0.0, when + pad_seconds))
            tick = max(tick, last_tick + 1)  # nunca dos eventos en el mismo tick
            events.append(Event.lyric(tick, text))
            last_tick = tick

        if phrase_end:
            end = sung_end
            # Nunca invadir el verso siguiente: su phrase_start se adelanta
            # PHRASE_LEAD, asi que hay que cerrar antes de eso.
            if index + 1 < len(lines):
                end = min(end, lines[index + 1].start - 2 * PHRASE_LEAD)
            if line.end is not None:
                end = min(end, line.end)
            tick = max(last_tick + 1,
                       tempo_map.tick_at_seconds(max(0.0, end + pad_seconds)))
            events.append(Event(tick, "phrase_end"))

    # En el mismo tick, cerrar antes de abrir.
    order = {"phrase_end": 0, "phrase_start": 1}
    events.sort(key=lambda e: (e.tick, order.get(e.text, 2)))
    return events
