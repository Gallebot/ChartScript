"""Standard MIDI Files: leer y escribir bytes. Sin saber nada de charts.

Este modulo solo entiende de cabeceras, chunks, delta-times y eventos. Quien
decide QUE notas van dentro es `backends/midi.py`; quien interpreta las que
vienen de fuera es `frontends/midi.py`.

Vive en la raiz del paquete y no dentro de `backends/` porque lo usan los dos
lados. Un contenedor no es un formato de salida: por el entra un MIDI ajeno y
por el sale un notes.mid, y ninguno de los dos deberia importar del otro.

Se escribe a mano en vez de con `mido` porque son un par de cientos de lineas y
el core del pipeline no tiene mas dependencias que ffmpeg, Pillow y librosa.
Meter una libreria para esto seria pagar mucho por poco.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

META_TEXT = 0x01
META_TRACK_NAME = 0x03
META_LYRIC = 0x05
META_TEMPO = 0x51
META_TIME_SIGNATURE = 0x58
META_END_OF_TRACK = 0x2F


def varint(value: int) -> bytes:
    """Entero de longitud variable, como los delta-times de MIDI.

    Siete bits por byte, y el bit alto marca que quedan mas. Un delta de 480 son
    dos bytes; uno de 60 son uno.
    """
    if value < 0:
        raise ValueError(f"varint negativo: {value}")
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


@dataclass(order=True)
class Event:
    """Un evento con su tick absoluto. El delta se calcula al serializar."""

    tick: int
    order: int
    """Desempate en el mismo tick. Los note-off van ANTES que los note-on: si en
    un tick acaba una nota y empieza otra en el mismo carril, el orden contrario
    apagaria la que acaba de empezar."""
    data: bytes = field(compare=False, default=b"")


def note_on(tick: int, pitch: int, velocity: int = 100, channel: int = 0) -> Event:
    return Event(tick, 1, bytes([0x90 | channel, pitch & 0x7F, velocity & 0x7F]))


def note_off(tick: int, pitch: int, channel: int = 0) -> Event:
    # Se emite un note-on de velocidad 0 y no un 0x80: es lo que hacen los charts
    # reales y permite aprovechar el running status, aunque aqui no se use.
    return Event(tick, 0, bytes([0x90 | channel, pitch & 0x7F, 0]))


def meta(tick: int, kind: int, payload: bytes, order: int = 0) -> Event:
    return Event(tick, order, bytes([0xFF, kind]) + varint(len(payload)) + payload)


def text(tick: int, kind: int, value: str) -> Event:
    """Los textos van en latin-1, que es lo que leen los juegos de esta familia."""
    return meta(tick, kind, value.encode("latin-1", errors="replace"))


def tempo(tick: int, bpm: float) -> Event:
    """MIDI guarda MICROSEGUNDOS POR NEGRA, no BPM.

    Es la contraparte de lo que hace el `.chart`, que guarda BPM * 1000. Los dos
    redondeos son distintos, asi que un mismo mapa de tempo escrito en los dos
    formatos no da exactamente los mismos tiempos. A 120 BPM la diferencia es de
    nanosegundos por negra; no se acumula a nada apreciable en una cancion.
    """
    micros = int(round(60_000_000.0 / bpm))
    return meta(tick, META_TEMPO, struct.pack(">I", micros)[1:])


def time_signature(tick: int, numerator: int, denominator: int) -> Event:
    """El denominador va como exponente de 2, igual que en el `.chart`."""
    exponent = denominator.bit_length() - 1
    return meta(tick, META_TIME_SIGNATURE,
                bytes([numerator, exponent, 24, 8]))


def track_chunk(events: list[Event]) -> bytes:
    """Serializa una pista, ordenando por tick y cerrando con end-of-track."""
    ordered = sorted(events)
    body = bytearray()
    previous = 0
    for event in ordered:
        body += varint(event.tick - previous) + event.data
        previous = event.tick
    body += varint(0) + bytes([0xFF, META_END_OF_TRACK, 0])
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def render(tracks: list[list[Event]], division: int = 480) -> bytes:
    """Archivo completo en formato 1: una pista de tempo y las demas en paralelo."""
    if not tracks:
        raise ValueError("Un SMF necesita al menos una pista.")
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), division)
    return header + b"".join(track_chunk(t) for t in tracks)


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------

class MidiReadError(RuntimeError):
    pass


@dataclass
class MidiTrack:
    """Una pista leida, con lo poco que hace falta para reconstruir notas."""

    name: str | None = None
    program: int | None = None
    """Instrumento General MIDI declarado, si lo declara."""
    channels: set[int] = field(default_factory=set)
    notes: list[tuple[int, int, int, int]] = field(default_factory=list)
    """(tick_inicio, tick_fin, altura, velocidad)."""
    texts: list[tuple[int, str]] = field(default_factory=list)
    tempos: list[tuple[int, float]] = field(default_factory=list)
    time_signatures: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def is_percussion(self) -> bool:
        """El canal 9 es percusion por convencion de General MIDI."""
        return 9 in self.channels


def read_varint(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    while True:
        if i >= len(data):
            raise MidiReadError("varint truncado al final del archivo")
        byte = data[i]
        i += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i


def parse(data: bytes) -> tuple[int, list[MidiTrack]]:
    """Devuelve (division, pistas). La division son ticks por negra."""
    if data[:4] != b"MThd":
        raise MidiReadError("no empieza por MThd: no es un Standard MIDI File")
    _, _, count, division = struct.unpack(">IHHH", data[4:14])
    if division & 0x8000:
        raise MidiReadError(
            "el archivo mide el tiempo en SMPTE y no en ticks por negra; "
            "no se soporta."
        )
    i, tracks = 14, []
    for _ in range(count):
        if data[i:i + 4] != b"MTrk":
            raise MidiReadError(f"se esperaba un chunk MTrk en el byte {i}")
        length = struct.unpack(">I", data[i + 4:i + 8])[0]
        tracks.append(_parse_track(data[i + 8:i + 8 + length]))
        i += 8 + length
    return division, tracks


def _parse_track(body: bytes) -> MidiTrack:
    track = MidiTrack()
    # Notas abiertas por (canal, altura): un note-on sin su note-off todavia.
    pending: dict[tuple[int, int], tuple[int, int]] = {}
    i, tick, status = 0, 0, None

    while i < len(body):
        delta, i = read_varint(body, i)
        tick += delta
        if i >= len(body):
            break

        if body[i] == 0xFF:
            kind = body[i + 1]
            length, i = read_varint(body, i + 2)
            payload, i = body[i:i + length], i + length
            if kind == META_TRACK_NAME and track.name is None:
                track.name = payload.decode("latin-1", "replace")
            elif kind in (META_TEXT, META_LYRIC):
                track.texts.append((tick, payload.decode("latin-1", "replace")))
            elif kind == META_TEMPO and payload:
                track.tempos.append((tick, 60_000_000.0 /
                                     int.from_bytes(payload, "big")))
            elif kind == META_TIME_SIGNATURE and len(payload) >= 2:
                track.time_signatures.append((tick, payload[0], 2 ** payload[1]))
            continue

        if body[i] in (0xF0, 0xF7):
            i += 1
            length, i = read_varint(body, i)
            i += length
            continue

        if body[i] & 0x80:
            status = body[i]
            i += 1
        if status is None:
            raise MidiReadError(f"evento sin estado en el byte {i}")

        kind, channel = status & 0xF0, status & 0x0F
        if kind in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
            pitch, velocity = body[i], body[i + 1]
            i += 2
            # Un note-on de velocidad 0 es un note-off: lo usan casi todos los
            # secuenciadores para aprovechar el running status.
            if kind == 0x90 and velocity:
                pending[(channel, pitch)] = (tick, velocity)
                track.channels.add(channel)
            elif kind in (0x80, 0x90):
                started = pending.pop((channel, pitch), None)
                if started is not None:
                    track.notes.append((started[0], tick, pitch, started[1]))
        elif kind == 0xC0:
            if track.program is None:
                track.program = body[i]
            i += 1
        elif kind == 0xD0:
            i += 1

    # Lo que quedo sonando se cierra al final: un archivo mal cerrado no deberia
    # perder sus notas, solo darles una duracion rara.
    for (_, pitch), (start, velocity) in pending.items():
        track.notes.append((start, tick, pitch, velocity))
    track.notes.sort()
    return track
