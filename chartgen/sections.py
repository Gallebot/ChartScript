"""Marcadores de seccion: Intro, Verse 1, Chorus 1...

Medido sobre 192 charts reales: el **80% llevan secciones**, con una mediana de
10 por cancion y una separacion tipica de 8 compases (p10 4, p90 16). La primera
es `Intro` en 108 de 154 casos.

Hay dos fuentes, y se prefiere la primera con diferencia:

1. **Marcadores de la tablatura.** Si el `.gp5` los trae, son exactos y con el
   nombre que puso una persona. No hay nada que inferir.
2. **Estructura del audio.** Cuando no hay tablatura o no trae marcadores, se
   segmenta la grabacion por auto-similitud. Encuentra los limites bastante
   bien; ponerles NOMBRE es otra cosa, y aqui se hace con una heuristica
   declarada: el bloque que mas se repite es el estribillo.

La deteccion de estructura buena vendria de `allin1`, que no se puede instalar en
Windows (ver README). Esto es el sustituto razonable, no su equivalente.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import RESOLUTION
from .ir import Event
from .tempo import TempoMap

MIN_SECTION_MEASURES = 6
"""Separacion minima entre secciones, en compases de 4/4.

Expresarlo en compases y no en segundos hace que valga igual a 90 que a 180 BPM;
con un umbral fijo de 8 segundos, 24K Magic salia con 21 secciones frente a las
10 de mediana.

El valor sale de medir contra los 12 marcadores puestos a mano en la tablatura de
Rolling in the Deep, que estan cada 8 compases exactos:

| minimo | detectadas | emparejadas | falsos positivos |
|---|---|---|---|
| 4 | 14 | 10 de 12 | 4 |
| **6** | **12** | **10 de 12** | **2** |
| 8 | 8 | 7 de 12 | 1 |

El umbral tiene que quedar POR DEBAJO de la separacion tipica, no igual: con 8
compases, cualquier limite detectado un poco corto se descartaba y se perdia un
tercio de las secciones."""

CLUSTERS = 6
"""Tipos de bloque distintos a buscar. Un tema pop tiene intro, verso,
prechorus, estribillo, puente y outro: seis."""


@dataclass
class Section:
    start: float
    """Segundos sobre el audio analizado."""
    name: str
    label: int = -1
    """Grupo de similitud. Los que comparten label suenan parecido."""
    energy: float = 0.0
    """Energia RMS media del bloque. 0 si no se midio."""


def normalize_name(raw: str) -> str:
    """Deja el nombre como lo escriben los charters: 'Verse 1', 'Chorus'."""
    text = " ".join(str(raw).split()).strip(" []")
    if not text:
        return "Section"
    return " ".join(word.capitalize() if word.islower() else word
                    for word in text.split())


def number_repeats(sections: list[Section]) -> list[Section]:
    """Numera los nombres repetidos: Verse, Verse -> Verse 1, Verse 2.

    Es lo que hacen los charts reales: 'chorus 1' y 'chorus 2' aparecen 77 veces
    cada uno en la coleccion, nunca 'chorus' dos veces seguidas.
    """
    counts: dict[str, int] = {}
    for section in sections:
        counts[section.name] = counts.get(section.name, 0) + 1

    seen: dict[str, int] = {}
    out = []
    for section in sections:
        name = section.name
        if counts[name] > 1:
            seen[name] = seen.get(name, 0) + 1
            name = f"{name} {seen[name]}"
        out.append(Section(section.start, name, section.label))
    return out


def from_markers(gp_song, timeline) -> list[Section]:
    """Secciones a partir de los marcadores de la tablatura. Fuente preferida.

    Los tiempos van en TICKS de salida, no en segundos: los marcadores ya estan
    en la rejilla musical y convertirlos a tiempo solo perderia precision.
    """
    out: list[Section] = []
    seen: set[int] = set()
    for played in timeline:
        header = gp_song.measureHeaders[played.index]
        marker = getattr(header, "marker", None)
        title = getattr(marker, "title", "") if marker else ""
        if not title or played.out_tick in seen:
            continue
        seen.add(played.out_tick)
        out.append(Section(start=float(played.out_tick), name=normalize_name(title)))
    return out


def _laplacian_labels(y, sr: int, beat_frames, hop_length: int, clusters: int):
    """Agrupacion espectral laplaciana: la receta estandar de librosa.

    Se probaron antes dos cosas mas simples y ninguna funciono. KMeans directo
    sobre la matriz de afinidad de cromas metia el 75% de los bloques en un solo
    grupo: en un tema montado sobre un mismo vamp la armonia no distingue nada.
    Añadir MFCC bajaba eso al 33% pero repartia el resto en grupos de uno, y el
    58% de las secciones acababan como "Interlude".

    La segmentacion laplaciana combina la matriz de recurrencia (que bloques se
    parecen) con la de camino (que bloques van seguidos), y de ahi salen los
    autovectores del laplaciano normalizado. Sobre 24K Magic produce una
    secuencia con alternancia clara, que es como se comporta un estribillo de
    verdad.
    """
    import librosa
    import numpy as np
    import scipy

    try:
        from sklearn.cluster import KMeans
    except ImportError as exc:
        # Mismo trato que pyphen en `lyrics.py`: una funcion opcional que no esta
        # instalada tiene que decir como instalarse, no soltar un traceback.
        raise RuntimeError(
            "Detectar secciones necesita scikit-learn:  "
            'pip install "chartgen[sections]"'
        ) from exc

    chroma = librosa.util.sync(
        librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length),
        beat_frames, aggregate=np.median)
    timbre = librosa.util.sync(
        librosa.feature.mfcc(y=y, sr=sr, hop_length=hop_length, n_mfcc=13),
        beat_frames, aggregate=np.median)

    # Que bloques se parecen, suavizado para quitar coincidencias sueltas.
    recurrence = librosa.segment.recurrence_matrix(chroma, width=3,
                                                   mode="affinity", sym=True)
    recurrence = scipy.ndimage.median_filter(recurrence, size=(1, 7))

    # Que bloques van seguidos, medido por cambio de timbre.
    distance = np.sum(np.diff(timbre, axis=1) ** 2, axis=0)
    sigma = np.median(distance) or 1.0
    similarity = np.exp(-distance / sigma)
    path = np.diag(similarity, 1) + np.diag(similarity, -1)

    # Equilibrio entre ambas, como en la receta original.
    degree_path = np.sum(path, axis=1)
    degree_rec = np.sum(recurrence, axis=1)
    total = degree_path + degree_rec
    weight = degree_path.dot(total) / (np.sum(total ** 2) or 1.0)
    combined = weight * recurrence + (1 - weight) * path

    laplacian = scipy.sparse.csgraph.laplacian(combined, normed=True)
    _, vectors = scipy.linalg.eigh(laplacian)
    vectors = scipy.ndimage.median_filter(vectors, size=(9, 1))
    scale = np.cumsum(vectors ** 2, axis=1) ** 0.5

    k = max(2, min(clusters, vectors.shape[1]))
    embedding = vectors[:, :k] / (scale[:, k - 1:k] + 1e-9)
    return KMeans(n_clusters=k, n_init=20, random_state=0).fit_predict(embedding)


def detect(audio, sr: int = 22050, hop_length: int = 512,
           clusters: int = CLUSTERS,
           min_measures: int = MIN_SECTION_MEASURES) -> list[Section]:
    """Segmenta la grabacion por auto-similitud y bautiza los bloques.

    Encuentra donde cambia la musica; el nombre lo pone `name_by_repetition`
    con reglas de estructura, porque nada en el audio dice "estribillo".

    Acertar los limites es realista. Acertar los nombres, menos: si la tablatura
    trae marcadores, usalos en su lugar.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio), mono=True, sr=sr)
    _, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop_length)
    if len(beat_frames) < clusters * 8:
        return []
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)

    labels = _laplacian_labels(y, sr, beat_frames, hop_length, clusters)

    # El minimo se expresa en compases, asi que depende del tempo detectado.
    periods = np.diff(beat_times)
    beat = float(np.median(periods)) if len(periods) else 0.5
    minimum = beat * 4 * min_measures

    bounds = [0] + [i for i in range(1, len(labels)) if labels[i] != labels[i - 1]]
    found: list[Section] = []
    for index in bounds:
        when = float(beat_times[min(index, len(beat_times) - 1)])
        if found and when - found[-1].start < minimum:
            continue
        found.append(Section(start=when, name="Section",
                             label=int(labels[index])))

    # Energia de cada bloque: es lo que separa estribillo de verso.
    loudness = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    frame_times = librosa.frames_to_time(np.arange(len(loudness)), sr=sr,
                                         hop_length=hop_length)
    duration = len(y) / sr
    for position, section in enumerate(found):
        end = (found[position + 1].start if position + 1 < len(found)
               else duration)
        window = (frame_times >= section.start) & (frame_times < end)
        section.energy = float(np.mean(loudness[window])) if window.any() else 0.0

    return name_by_repetition(found)


def name_by_repetition(sections: list[Section]) -> list[Section]:
    """Bautiza los bloques nombrando SOLO lo que se puede justificar.

    Esto se midio contra los 12 marcadores puestos a mano en la tablatura de
    Rolling in the Deep, que es verdad absoluta. Resultado en dos partes muy
    distintas:

    - **Limites: 10 de 12 emparejados, error mediano 0.6 s.** Fiables.
    - **Nombres: 0 de 10 con reglas de repeticion.** Cero.

    Añadir energia arreglo los estribillos (las secciones fuertes son 0.2099 de
    RMS medio frente a 0.1813 las flojas, sin solaparse) y subio a 2 de 10. El
    resto no. Verso, prechorus e interludio no se distinguen con lo que hay: la
    regla "lo que precede al estribillo" encontraba la posicion correcta pero el
    humano la llamaba Prechorus, no Verse.

    Asi que se nombra lo justificado y se deja el resto en letras:

    - **Intro** y **Outro**: primero y ultimo. Trivialmente correctos.
    - **Chorus**: el grupo repetido de mayor energia. Verificado.
    - **Section A, B, C...**: todo lo demas, por grupo de similitud.

    Un "Section B" no dice mucho, pero un "Verse 3" equivocado dice algo falso.
    Si la tablatura trae marcadores son mejores: usalos.
    """
    if not sections:
        return []
    if len(sections) <= 2:
        return number_repeats([Section(s.start, "Intro" if i == 0 else "Outro",
                                       s.label, s.energy)
                               for i, s in enumerate(sections)])

    middle = range(1, len(sections) - 1)
    counts: dict[int, int] = {}
    energies: dict[int, list[float]] = {}
    for i in middle:
        label = sections[i].label
        counts[label] = counts.get(label, 0) + 1
        energies.setdefault(label, []).append(sections[i].energy)

    if not counts:
        counts = {sections[0].label: 1}
        energies = {sections[0].label: [sections[0].energy]}

    repeated = [label for label, n in counts.items() if n >= 2]
    measured = any(s.energy > 0 for s in sections)
    if repeated and measured:
        chorus = max(repeated,
                     key=lambda label: sum(energies[label]) / len(energies[label]))
    elif repeated:
        chorus = max(repeated, key=lambda label: counts[label])
    else:
        chorus = None

    letters: dict[int, str] = {}
    names: list[str] = []
    for index, section in enumerate(sections):
        if index == 0:
            names.append("Intro")
        elif index == len(sections) - 1:
            names.append("Outro")
        elif section.label == chorus:
            names.append("Chorus")
        else:
            if section.label not in letters:
                letters[section.label] = chr(ord("A") + len(letters) % 26)
            names.append(f"Section {letters[section.label]}")

    return number_repeats([Section(s.start, name, s.label, s.energy)
                           for s, name in zip(sections, names)])


def _durations(sections: list[Section]) -> list[float]:
    """Duracion de cada seccion; la ultima hereda la mediana de las demas."""
    spans = [b.start - a.start for a, b in zip(sections, sections[1:])]
    if not spans:
        return [0.0]
    spans.append(sorted(spans)[len(spans) // 2])
    return spans


def to_events(sections: list[Section], tempo_map: TempoMap,
              pad_seconds: float = 0.0, origin_tick: int = 0,
              snap_to_measure: bool = True, in_ticks: bool = False) -> list[Event]:
    """Convierte secciones en eventos `E "section ..."`.

    Se encajan al compas mas cercano: un marcador a medio compas se ve mal en
    Moonscraper y no corresponde a como se escribe la musica.
    """
    events: list[Event] = []
    for section in sections:
        tick = (int(section.start) if in_ticks
                else tempo_map.tick_at_seconds(max(0.0, section.start + pad_seconds)))
        if snap_to_measure:
            measure = tempo_map.ticks_per_measure_at(tick) or RESOLUTION * 4
            offset = tick - origin_tick
            tick = origin_tick + round(offset / measure) * measure
        events.append(Event.section(max(0, tick), section.name))

    events.sort(key=lambda e: e.tick)
    deduped: list[Event] = []
    for event in events:
        if deduped and deduped[-1].tick == event.tick:
            continue
        deduped.append(event)
    return deduped
