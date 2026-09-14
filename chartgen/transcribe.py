"""Transcripcion de audio a notas: audio -> alturas y tiempos en segundos.

Frontend intercambiable, igual que `beats.py`: aqui viven los detectores y el
tipo que devuelven; quien convierte eso en un chart es
`chartgen.frontends.transcription`, que no sabe que modelo lo alimento.

## Que se puede esperar de esto

Un transcriptor no devuelve la partitura: devuelve una hipotesis ruidosa. Sobre
una mezcla completa, un modelo polifonico generico (Basic Pitch y parecidos)
confunde armonicos de la voz con notas y entierra el bajo. Por eso el orden del
roadmap es separar primero (Demucs) y transcribir despues, y por eso el primer
detector que hay aqui es MONOFONICO y para bajo: es el caso en el que un
estimador de f0 clasico se sostiene sin red neuronal ni GPU.

## Por que pYIN y no un modelo

`pYIN` (Mauch & Dixon) esta en librosa, que ya es dependencia del core. Da f0 y
probabilidad de sonoridad cuadro a cuadro, que es todo lo que hace falta cuando
la fuente es monofonica. Cero dependencias nuevas y sirve de linea base contra la
que medir lo que venga despues; si un modelo de 4 GB no le gana, no vale la pena.

## Lo que este modulo NO decide

Nada de carriles, ticks ni compases. Aqui todo esta en segundos y en altura MIDI.
La cuantizacion contra el mapa de tempo es la que decide si el chart se siente
fiel, y vive en el frontend para poder probarla sin tocar un archivo de audio.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CACHE_VERSION = 2
"""Sube cuando cambia lo que devuelven los detectores, para que quien cachee
transcripciones sepa que las suyas son de otra epoca. La v2 añade `salience`."""


class TranscriptionError(RuntimeError):
    pass


@dataclass
class TranscribedNote:
    """Una nota detectada. Tiempos en segundos sobre el audio ANALIZADO.

    Es decir: sobre el archivo original, sin el lead-in que añade el empaquetado.
    Quien la lleve a ticks tiene que sumar `PAD_SECONDS`, igual que hace el
    encaje de tablaturas.
    """

    start: float
    end: float
    pitch: float
    """Altura MIDI. Fraccionaria a proposito: el redondeo lo decide quien la use,
    y el desvio respecto al semitono delata errores del detector."""
    confidence: float = 1.0
    """0 a 1. Lo seguro que estaba el detector de la ALTURA. Ojo: no mide si la
    nota merece estar en un chart. Ver `salience`."""
    salience: float = 1.0
    """Fuerza del ataque, en multiplos de la mediana de la cancion.

    1.0 es "un ataque del monton"; 3.0 es un golpe claro. Es la unica medida que
    distingue una nota charteable de una que no lo es. Ver `MIN_SALIENCE`."""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"Nota que acaba antes de empezar: {self.start}")

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def midi(self) -> int:
        return int(round(self.pitch))


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio: Path) -> list[TranscribedNote]: ...


def to_json(notes: list[TranscribedNote]) -> str:
    """Formato de intercambio con los detectores que viven en otro venv."""
    return json.dumps([
        {"start": n.start, "end": n.end, "pitch": n.pitch,
         "confidence": n.confidence, "salience": n.salience}
        for n in notes
    ])


def from_json(text: str) -> list[TranscribedNote]:
    return [
        TranscribedNote(d["start"], d["end"], d["pitch"], d.get("confidence", 1.0),
                        d.get("salience", 1.0))
        for d in json.loads(text)
    ]


# --------------------------------------------------------------------------
# Limpieza
# --------------------------------------------------------------------------
#
# Ningun detector devuelve algo utilizable en crudo. Estas funciones son las que
# convierten la hipotesis en algo charteable, y estan aparte del detector a
# proposito: se prueban con notas sinteticas, sin audio, y valen igual para lo
# que venga despues (Basic Pitch trocea notas y se equivoca de octava mas que
# pYIN, no menos).

def drop_short(notes: list[TranscribedNote],
               min_duration: float = 0.05) -> list[TranscribedNote]:
    """Descarta lo que dura menos que un adorno.

    50 ms es aproximadamente una fusa a 150 BPM. Por debajo de eso no hay nota
    que charter: son transitorios, respiraciones de cuerda y trozos de ataque
    que el segmentador partio de mas.
    """
    return [n for n in notes if n.duration >= min_duration]


def merge_repeats(notes: list[TranscribedNote],
                  max_gap: float = 0.02) -> list[TranscribedNote]:
    """Une notas contiguas de la misma altura separadas por casi nada.

    Los modelos que estiman nota a nota (Basic Pitch) trocean una nota larga en
    varias cuando la energia fluctua, y esto las recompone.

    **Esto no se puede resolver del todo con solo las notas.** Una nota troceada
    deja un hueco de uno o dos cuadros de analisis; dos semicorcheas repetidas a
    150 BPM empiezan con 100 ms de diferencia, pero si la primera decae en 90 ms
    el hueco entre ellas es tambien de 10. Vistas como (inicio, fin, altura) son
    el mismo dato, y cualquier umbral que recomponga la una fusiona la otra.

    Lo que si distingue los dos casos es que la repeticion tiene ATAQUE y la
    nota troceada no. Por eso `PyinBass` no usa esta funcion: corta con los
    onsets y el problema no llega a existir. Queda aqui para los transcriptores
    que no dan ataques, con el umbral en dos cuadros y asumiendo el error.
    """
    out: list[TranscribedNote] = []
    for note in sorted(notes, key=lambda n: n.start):
        if out and out[-1].midi == note.midi and note.start - out[-1].end <= max_gap:
            previous = out[-1]
            out[-1] = TranscribedNote(
                previous.start, max(previous.end, note.end), previous.pitch,
                max(previous.confidence, note.confidence),
            )
        else:
            out.append(note)
    return out


def fix_octaves(notes: list[TranscribedNote], window: int = 4,
                tolerance: float = 1.0) -> list[TranscribedNote]:
    """Baja o sube a su sitio las notas que se fueron una octava justa.

    Es el error tipico de un estimador de f0: engancharse al primer armonico y
    dar la nota una octava arriba. Se detecta porque el salto es de 12 o 24
    semitonos EXACTOS respecto a lo que hacen sus vecinas, cosa que una linea de
    bajo real casi nunca hace de una nota a la siguiente.

    Solo se corrige si el vecindario es coherente: si las vecinas ya estan
    repartidas por dos octavas, no hay a que volver y se deja como esta.
    """
    import statistics

    ordered = sorted(notes, key=lambda n: n.start)
    out: list[TranscribedNote] = []
    for index, note in enumerate(ordered):
        low = max(0, index - window // 2)
        neighbours = [n.pitch for n in ordered[low:low + window + 1]
                      if n is not note]
        if len(neighbours) < 2:
            out.append(note)
            continue
        centre = statistics.median(neighbours)
        spread = max(neighbours) - min(neighbours)
        delta = note.pitch - centre
        octaves = round(delta / 12.0)
        coherent = spread <= 12.0
        if coherent and octaves != 0 and abs(delta - octaves * 12) <= tolerance:
            out.append(TranscribedNote(note.start, note.end,
                                       note.pitch - octaves * 12, note.confidence))
        else:
            out.append(note)
    return out


def enforce_monophonic(notes: list[TranscribedNote]) -> list[TranscribedNote]:
    """Una voz sola: ninguna nota empieza antes de que acabe la anterior.

    Los charts reales de bajo son monofonicos el 99.5% del tiempo (medido sobre
    la coleccion de referencia), asi que solapes en una linea de bajo son error
    del detector, no polifonia. Se recorta en vez de descartar: el ataque estaba
    bien, lo que sobraba era la cola.
    """
    ordered = sorted(notes, key=lambda n: (n.start, -n.confidence))
    out: list[TranscribedNote] = []
    for note in ordered:
        if out and note.start < out[-1].end:
            previous = out[-1]
            if note.start <= previous.start:
                continue  # empieza a la vez o antes: es la misma nota duplicada
            out[-1] = TranscribedNote(previous.start, note.start, previous.pitch,
                                      previous.confidence)
        out.append(note)
    return [n for n in out if n.duration > 0]


PEAK_DENSITY = {"bass": 5.6, "guitar": 8.0, "drums": 6.6}
"""Notas por segundo como maximo, medido en ventanas de 10 s.

Medido sobre los charts de la coleccion de referencia (22 con bajo, 191 con
guitarra, 78 con bateria), contando eventos y no notas: los acordes cuentan una
vez. Para cada chart se tomo la ventana de 10 s mas densa y de esa distribucion
sale el percentil 90:

| instrumento | ev/s global (mediana) | pico en 10 s (mediana) | pico (p90) |
|---|---|---|---|
| bajo | 1.84 | 2.90 | 5.6 |
| guitarra | 3.01 | 5.00 | 8.0 |
| bateria | 3.61 | 4.75 | 6.6 |

Es el numero que sustituye a elegir un umbral de confianza a ojo. Un tramo mas
denso que el 90% de lo que escriben los humanos no es virtuosismo detectado: son
falsos positivos, que ademas se amontonan justo donde la mezcla es mas confusa.
"""


MIN_SALIENCE = 0.0
"""Ataque minimo para que una nota entre en el chart. 0 = no se filtra.

## Que mide

Un transcriptor responde "que suena". Un chart responde otra cosa: "que merece
ser una nota". Sobre canciones sin guitarra de verdad — rap, R&B, K-pop — el stem
`other` esta lleno de sintetizadores y pads sostenidos que pYIN transcribe con
toda la razon, y el charter humano no escribe ninguno. Medido: en esas canciones
se generaban hasta el triple de notas que el humano y la precision caia al 14%.

## Que separa una de otra, medido

Se etiquetaron 6.465 notas de 14 canciones segun si les correspondia una nota
humana a menos de 80 ms, y se comparo cada rasgo disponible:

| rasgo | mediana en las buenas | mediana en las sobrantes |
|---|---|---|
| **ataque (salience)** | **2.67** | **1.27** |
| duracion | 0.151 s | 0.139 s |
| confianza de pYIN | 0.100 | 0.080 |
| energia (RMS) | 1.31 | 1.12 |
| hueco con la anterior | 0.221 s | 0.186 s |

Solo el ataque separa, y separa mucho. Lo demas es ruido. En una de las
canciones la confianza de pYIN era **mayor** en las notas sobrantes que en las
buenas: el modelo esta comodisimo siguiendo un pad, que es justo lo que no
queremos. Tiene sentido musical: una nota charteable se ataca, un pad se abre.

## Por que sale a cero: no funciona

Sobre notas sueltas el filtro promete mucho. Sobre 14 canciones etiquetadas, con
umbral 1.2 se quedaba el 85% de las buenas y solo el 57% de las sobrantes, y la
precision subia del 59% al 68%.

Medido sobre el chart terminado, en 30 canciones, no se cumple:

| umbral | F1 | precision | recall | notas |
|---|---|---|---|---|
| **sin filtro** | **34.8%** | 46.7% | 30.1% | 12.930 |
| 1.2 | 23.9% | 50.7% | 17.0% | 6.223 |
| 1.5 | 21.2% | 52.4% | 14.4% | 5.149 |
| 2.0 | 15.4% | 53.2% | 11.0% | 3.714 |

La precision sube 4 puntos y el recall se parte por la mitad. El F1 cae un tercio
con el ajuste mas suave.

Por que la promesa no se cumplio: la medida sobre notas sueltas se hizo sobre las
CRUDAS, y para cuando llega este filtro la limpieza ya ha quitado buena parte de
lo que iba a quitar. Lo que queda por filtrar ya no es basura facil, y el umbral
empieza a comerse notas buenas. Es la diferencia entre medir una pieza aislada y
medirla dentro del sistema, y solo se ve con el banco de pruebas montado.

Se deja como opcion (`--min-salience`) porque el compromiso es real y hay a quien
le sirva un chart con menos notas y mas limpias, pero **por defecto no se filtra**.
Lo que si se hace siempre es usar este numero, y no la confianza, para decidir a
quien se descarta cuando hay que descartar."""


def keep_salient(notes: list[TranscribedNote],
                 threshold: float = MIN_SALIENCE) -> list[TranscribedNote]:
    """Se queda con las notas cuyo ataque llega al umbral."""
    if threshold <= 0:
        return list(notes)
    return [n for n in notes if n.salience >= threshold]


HUMAN_DENSITY = {
    "bass": (1.01, 1.84, 3.59),
    "guitar": (1.68, 3.01, 4.78),
    "drums": (2.29, 3.61, 5.00),
}
"""(p10, mediana, p90) de eventos por segundo en los charts humanos.

Medido sobre la coleccion de referencia: 22 charts con bajo, 191 con guitarra y
78 con bateria, contando eventos y no notas (un acorde cuenta una vez) sobre el
tramo entre la primera y la ultima nota.

Sirve para lo unico que se puede juzgar de un chart sin tener otro con el que
compararlo: si tiene un numero de notas plausible. No dice si las notas estan
donde deben, pero **si la densidad se sale del rango, el chart esta mal seguro**,
y eso se puede avisar en vez de dejar que el usuario lo descubra jugando."""


def density_verdict(notes: int, seconds: float,
                    instrument: str) -> tuple[float, str, bool]:
    """Compara la densidad generada con la de los charts humanos.

    Devuelve (densidad, veredicto, hay_problema). El veredicto esta pensado para
    imprimirse tal cual: es el aviso que de otro modo habria que pedirle a
    alguien que mirase los numeros y lo dijera.
    """
    density = notes / seconds if seconds > 0 else 0.0
    rango = HUMAN_DENSITY.get(instrument)
    if rango is None:
        return density, f"{density:.2f} notas/s", False

    low, middle, high = rango
    marco = f"{density:.2f} notas/s (humano {low:.2f}-{high:.2f}, tipico {middle:.2f})"
    if density < low:
        return density, (
            f"{marco}\n  por DEBAJO de lo que escribe el 90% de los charts "
            f"humanos: va a sentirse vacio."
        ), True
    if density > high:
        return density, (
            f"{marco}\n  por ENCIMA de lo que escribe el 90% de los charts "
            f"humanos: probablemente sobran notas."
        ), True
    return density, f"{marco}: dentro de lo normal.", False


def limit_density(notes: list[TranscribedNote], ceiling: float,
                  window: float = 10.0) -> list[TranscribedNote]:
    """Recorta los tramos mas densos que lo que chartea un humano.

    Se descarta por ATAQUE, no por confianza. La confianza era lo primero que
    parecia razonable y esta medido que no separa: ver `MIN_SALIENCE`. Ordenar
    por ella era tirar notas casi al azar.

    Se hace por ventana y no sobre la cancion entera: un solo tramo sucio no
    tiene por que vaciar los tramos limpios, y al reves, una cancion tranquila
    con un unico pasaje malo se salvaria con un promedio global.
    """
    if ceiling <= 0 or not notes:
        return list(notes)

    ordered = sorted(notes, key=lambda n: n.start)
    allowed = max(1, int(ceiling * window))
    doomed: set[int] = set()

    start = ordered[0].start
    while start <= ordered[-1].start:
        inside = [i for i, n in enumerate(ordered)
                  if start <= n.start < start + window and i not in doomed]
        if len(inside) > allowed:
            weakest = sorted(inside, key=lambda i: ordered[i].salience)
            doomed.update(weakest[:len(inside) - allowed])
        start += window

    return [n for i, n in enumerate(ordered) if i not in doomed]


def clean(notes: list[TranscribedNote], instrument: str = "bass",
          min_duration: float = 0.05, monophonic: bool | None = None,
          octaves: bool = True, density: bool = True,
          min_salience: float = MIN_SALIENCE,
          ceiling: float | None = None) -> list[TranscribedNote]:
    """Pasa la limpieza completa en el orden en que tiene sentido.

    El orden importa: quitar la basura corta antes de mirar octavas evita que
    un transitorio de 20 ms cuente como vecino, y limitar la densidad al final
    permite que las notas ya corregidas compitan por el cupo en igualdad.
    """
    if monophonic is None:
        monophonic = instrument in ("bass", "vocals")

    out = drop_short(notes, min_duration)
    out = keep_salient(out, min_salience)
    if octaves:
        out = fix_octaves(out)
    if monophonic:
        out = enforce_monophonic(out)
    if density:
        if ceiling is None:
            ceiling = PEAK_DENSITY.get(instrument, 0.0)
        out = limit_density(out, ceiling)
    return out


# --------------------------------------------------------------------------
# Detector de bajo con pYIN
# --------------------------------------------------------------------------

class PyinBass:
    """Bajo monofonico con pYIN + onsets, todo con librosa.

    Dos señales, cada una para lo que sabe hacer:

    - Los **onsets en la banda de graves** dan el TIEMPO. Es la misma envolvente
      que usa el encaje de tablaturas, que ya esta calibrada: mirar solo la banda
      del instrumento es lo que hace que el bajo no quede enterrado bajo la caja
      y la voz.
    - **pYIN** da la ALTURA. Su resolucion temporal es peor (un cuadro son 23 ms)
      pero eso da igual: el ataque lo marca el onset.

    El tercer detalle es que hay notas SIN ataque percusivo: ligados, slides y
    cualquier cosa que el bajista toque sin volver a pulsar. Ahi el unico aviso
    es que la altura cambia y se queda cambiada, asi que los cortes salen de la
    union de ambas cosas: onsets y cambios de altura sostenidos.
    """

    name = "pyin/bass"

    def __init__(
        self,
        sr: int = 22050,
        onset_hop: int = 256,
        pitch_hop: int = 512,
        fmin: float = 30.0,
        fmax: float = 400.0,
        frame_length: int = 2048,
        band: tuple[float, float] = (0.0, 250.0),
        min_voiced: float = 0.4,
        min_run: int = 3,
        attack_skip: float = 0.03,
        min_gap: float = 0.06,
    ) -> None:
        self.sr = sr
        self.onset_hop = onset_hop
        self.pitch_hop = pitch_hop
        self.fmin = fmin
        """30 Hz es un si0: la nota mas grave de un bajo de cinco cuerdas."""
        self.fmax = fmax
        self.frame_length = frame_length
        """pYIN necesita ventana larga para alturas graves: a 22050 Hz y 30 Hz de
        minimo hacen falta al menos 1470 muestras, y 2048 es la potencia de 2 que
        cabe encima."""
        self.band = band
        self.min_voiced = min_voiced
        """Fraccion de cuadros con altura estimable para aceptar el segmento."""
        self.min_run = min_run
        """Cuadros seguidos con la misma altura para creerse un cambio sin ataque."""
        self.attack_skip = attack_skip
        """Segundos de ataque que se ignoran al medir la altura. El transitorio
        de una cuerda pulsada no tiene todavia altura definida."""
        self.min_gap = min_gap
        """Distancia minima entre dos cortes. Manda el onset, que es mas preciso."""

    # -- deteccion ---------------------------------------------------------

    def transcribe(self, audio: Path) -> list[TranscribedNote]:
        import numpy as np

        f0, voiced_prob, pitch_times = self._pitch(audio)
        onsets, env, env_times = self._onsets(audio)

        midi = np.full(len(f0), np.nan)
        usable = ~np.isnan(f0)
        if not usable.any():
            raise TranscriptionError(
                "pYIN no encontro ninguna altura estimable. Si el audio es una "
                "mezcla completa, separa el bajo antes (Demucs)."
            )
        import librosa
        midi[usable] = librosa.hz_to_midi(f0[usable])

        cuts = self._cut_points(midi, pitch_times, onsets)
        notes = self._segments_to_notes(cuts, midi, voiced_prob, pitch_times,
                                        env, env_times)
        if not notes:
            raise TranscriptionError(
                "No se pudo formar ninguna nota: hay altura pero no segmentos "
                "estables. Revisa que el audio sea monofonico."
            )
        return notes

    def _pitch(self, audio: Path):
        import librosa
        import numpy as np

        y, sr = librosa.load(str(audio), mono=True, sr=self.sr)
        f0, _, voiced_prob = librosa.pyin(
            y, fmin=self.fmin, fmax=self.fmax, sr=sr,
            frame_length=self.frame_length, hop_length=self.pitch_hop,
        )
        times = librosa.times_like(f0, sr=sr, hop_length=self.pitch_hop)
        return np.asarray(f0), np.nan_to_num(np.asarray(voiced_prob)), times

    def _onsets(self, audio: Path):
        """Ataques en la banda del instrumento, y la envolvente normalizada.

        La envolvente se divide por su mediana para que la salience de una nota
        signifique lo mismo en una grabacion suave que en una comprimida: "este
        ataque es N veces el ataque tipico de esta cancion".
        """
        import librosa
        import numpy as np

        from .align import onset_envelope

        env, times = onset_envelope(audio, self.band, sr=self.sr,
                                    hop_length=self.onset_hop)
        if len(env) == 0:
            return np.array([]), env, times
        positive = env[env > 0]
        env = env / (float(np.median(positive)) if len(positive) else 1.0)
        frames = librosa.onset.onset_detect(onset_envelope=env, sr=self.sr,
                                            hop_length=self.onset_hop,
                                            backtrack=True)
        frames = np.clip(frames, 0, len(times) - 1)
        return times[frames], env, times

    # -- segmentacion ------------------------------------------------------

    def _stable_changes(self, midi, times):
        """Instantes donde la altura cambia y SE QUEDA cambiada.

        Un vibrato o un error suelto cruzan la frontera del semitono y vuelven;
        exigir `min_run` cuadros seguidos en el valor nuevo los descarta sin
        tener que suavizar la señal, que borraria los cambios de verdad.
        """
        import numpy as np

        rounded = np.round(midi)
        changes = []
        current = None
        index = 0
        while index < len(rounded):
            value = rounded[index]
            if np.isnan(value):
                index += 1
                continue
            run = index
            while run < len(rounded) and rounded[run] == value:
                run += 1
            if run - index >= self.min_run and value != current:
                if current is not None:
                    changes.append(float(times[index]))
                current = value
            index = run
        return changes

    def _cut_points(self, midi, times, onsets) -> list[float]:
        """Union de ataques y cambios de altura, sin cortes pegados.

        Cuando un cambio de altura cae junto a un ataque son el mismo evento y
        gana el ataque: su resolucion es de 12 ms frente a los 23 del estimador
        de altura, y ademas marca el inicio real del sonido y no el momento en
        que la altura se estabiliza.
        """
        cuts = sorted([(t, True) for t in onsets]
                      + [(t, False) for t in self._stable_changes(midi, times)])
        kept: list[float] = []
        kept_is_onset: list[bool] = []
        for time, is_onset in cuts:
            if kept and time - kept[-1] < self.min_gap:
                if is_onset and not kept_is_onset[-1]:
                    kept[-1], kept_is_onset[-1] = time, True
                continue
            kept.append(time)
            kept_is_onset.append(is_onset)

        first_sound = float(times[0]) if len(times) else 0.0
        if not kept or kept[0] > first_sound:
            kept.insert(0, first_sound)
        return kept

    def _segments_to_notes(self, cuts, midi, voiced_prob, times,
                           env=None, env_times=None):
        import numpy as np

        def salience_at(second: float) -> float:
            """Pico de la envolvente en el entorno del ataque.

            Se mira una ventana de tres cuadros y no el valor exacto porque el
            ataque no cae necesariamente en el centro de un cuadro de analisis.
            """
            if env is None or env_times is None or len(env) == 0:
                return 1.0
            index = int(np.searchsorted(env_times, second))
            index = min(max(index, 0), len(env) - 1)
            return float(env[max(0, index - 1):index + 2].max())

        end_of_audio = float(times[-1]) if len(times) else 0.0
        bounds = list(cuts) + [end_of_audio]
        notes: list[TranscribedNote] = []

        for start, stop in zip(bounds, bounds[1:]):
            if stop - start <= 0:
                continue
            low = int(np.searchsorted(times, start + self.attack_skip))
            high = int(np.searchsorted(times, stop))
            if high - low < 1:  # segmento mas corto que el salto de analisis
                low = int(np.searchsorted(times, start))
                high = max(low + 1, int(np.searchsorted(times, stop)))
            window = midi[low:high]
            if len(window) == 0:
                continue
            voiced = window[~np.isnan(window)]
            if len(voiced) < max(1, self.min_voiced * len(window)):
                continue  # mas silencio que nota: no hay nada que charter

            # La altura es la MEDIANA, no la media: un cuadro que se fue una
            # octava mueve la media medio tono y a la mediana nada.
            pitch = float(np.median(voiced))
            confidence = float(np.mean(voiced_prob[low:high])) if high > low else 0.0

            # El final es el ultimo cuadro con altura, no el corte siguiente: la
            # nota puede haber muerto mucho antes de que empiece la de despues.
            last_voiced = np.flatnonzero(~np.isnan(midi[low:high]))
            end = (float(times[low + int(last_voiced[-1])])
                   if len(last_voiced) else stop)
            notes.append(TranscribedNote(start, max(end, start + 1e-3), pitch,
                                         min(1.0, confidence),
                                         salience_at(start)))
        return notes


def get_transcriber(name: str, **kwargs) -> Transcriber:
    if name in ("pyin", "pyin/bass"):
        return PyinBass(**kwargs)
    raise ValueError(f"Transcriptor desconocido: {name}")
