"""El flujo MIDI -> chart, partido en lo caro y lo barato.

## Por que esta partido

Generar N variantes de asignacion de canales no puede repetir N veces lo que no
depende de la asignacion. Detectar beats cuesta segundos y MusicBrainz limita a
una peticion por segundo: hacerlo cinco veces para cinco variantes de la MISMA
cancion seria absurdo, y encima las variantes podrian salir con mapas de tempo
distintos si la deteccion no es determinista.

Asi que:

- `prepare()` se ejecuta **una vez**: lee el MIDI, fija el mapa de tempo, busca
  metadatos y caratula, mide el preview. Devuelve un `Prepared`.
- `build_variant()` se ejecuta **una vez por variante** y solo hace lo que
  depende de la asignacion: cuantizar, reducir a carriles y empaquetar.

## De donde sale el mapa de tempo

Por orden de preferencia:

1. **Del audio**, si hay un archivo de audio con el mismo nombre al lado del
   MIDI. Es lo bueno: la grabacion respira y el mapa ajustado la sigue.
2. **Del propio MIDI**, si no hay audio. Se acepta porque sin audio no hay
   alternativa, pero el chart solo encajara con la grabacion en la medida en que
   el MIDI acertara el tempo. Y al no haber audio, `song.ogg` sale en silencio:
   sirve para editar en Moonscraper, no para jugar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from chartgen import PAD_SECONDS, RESOLUTION
from chartgen import audio as audio_mod
from chartgen import beats as beats_mod
from chartgen import drums as drums_mod
from chartgen import lyrics as lyrics_mod
from chartgen import metadata as meta_mod
from chartgen import package, sections as sections_mod, smf, tempo_fit
from chartgen import transcribe as transcribe_mod
from chartgen.charting import add_star_power
from chartgen.frontends import midi as midi_frontend
from chartgen.frontends import transcription as tx_frontend
from chartgen.ir import Metadata, Note, Song, Track
from chartgen.tempo import TempoEvent, TempoMap, TimeSignature, quantize_bpm

CACHE_DIR = Path.home() / ".cache" / "chartgen" / "musicbrainz"

AUDIO_EXTENSIONS = (".ogg", ".mp3", ".opus", ".m4a", ".wav", ".flac")
"""Por orden de preferencia. `.ogg` primero porque es lo que acaba en la carpeta
y saltarse la reconversion evita una generacion de perdida mas."""

MELODIC = ("guitar", "bass", "rhythm", "coop", "keys")
"""Canales que pasan por el frontend melodico (reduccion a cinco carriles)."""

DRUMS = "drums"

CHANNELS = MELODIC + (DRUMS,)
"""Todo lo que un backend sabe escribir. `vocals` necesita una pista de voces
en el `.mid` que ningun backend de aqui genera todavia, asi que no se ofrece."""

MAX_SIMULTANEOUS = 3
"""Golpes de bateria a la vez. Medido en la coleccion de referencia: 35.277
eventos de uno, 22.694 de dos, 1.201 de tres y practicamente ninguno de cuatro."""

DRUMS_DROP_BUDGET = 0.05
"""Fraccion de golpes de bateria que se acepta perder por caer fuera de rejilla.

Existe porque `choose_subdivision` no sirve tal cual para percusion. Ese criterio
elige la rejilla mas GRUESA que explique un 80% de los ataques (`MIN_SHARE`), y en
la primera prueba real eso eligio corcheas y dejo 99 de 526 golpes fuera: casi una
quinta parte de la bateria desaparecida.

El 80% es un umbral razonable para una linea melodica, donde un adorno fuera de
rejilla es ruido del detector. En bateria no: un bombo que falta se nota al jugar,
y uno 20 ms desplazado no. Asi que aqui se baja el presupuesto de perdida y se
afina la rejilla hasta cumplirlo."""


class PipelineError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Audio hermano
# --------------------------------------------------------------------------

def find_audio(midi: Path) -> Path | None:
    """Busca un audio con el mismo nombre que el MIDI, en su misma carpeta.

    Es la convencion menos sorprendente: si arrastras `Artista - Titulo.mid` y
    al lado tienes `Artista - Titulo.mp3`, es evidente que van juntos.
    """
    for ext in AUDIO_EXTENSIONS:
        candidate = midi.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


# --------------------------------------------------------------------------
# Mapa de tempo
# --------------------------------------------------------------------------

def tempo_from_audio(audio_path: Path, detector: str = "auto",
                     bpm_hint: float | None = None,
                     max_error: float = tempo_fit.DEFAULT_MAX_ERROR,
                     log=print):
    """Ajusta el SyncTrack a la grabacion. Mismo criterio que usa el CLI."""
    if detector != "auto":
        det = beats_mod.get_detector(detector, bpm_hint=bpm_hint)
        return tempo_fit.fit(det.detect(audio_path), max_error=max_error)

    candidates = {
        "librosa/plp": beats_mod.LibrosaBeats(bpm_hint=bpm_hint).detect(audio_path),
        "librosa/beat_track": beats_mod.LibrosaBeats(
            method="beat_track", bpm_hint=bpm_hint, snap=False,
            prune=False).detect(audio_path),
    }
    if beats_mod.default_beat_this_python().exists():
        try:
            candidates["beat_this"] = beats_mod.BeatThisBeats().detect(audio_path)
        except beats_mod.BeatDetectionError as exc:
            log(f"  beat_this no disponible: {str(exc)[:70]}")
    chosen, _, fit = tempo_fit.best_fit(candidates, max_error=max_error)
    log(f"  elegido: {chosen}")
    return fit


def tempo_from_midi(midi: Path, resolution: int = RESOLUTION,
                    pad_seconds: float = PAD_SECONDS) -> tuple[TempoMap, int, str]:
    """Mapa de tempo tomado del propio MIDI. Devuelve (mapa, origin_tick, nota).

    Reproduce la convencion de `tempo_fit.fit`, que es la que espera el resto del
    pipeline: el tick 0 es el principio del audio YA con lead-in, `origin_tick`
    cae en un limite de compas y entre ambos hay un solo evento de tempo cuyo BPM
    se elige para que `origin_tick` valga exactamente `pad_seconds`.

    Los ticks del MIDI se reescalan de su division a la nuestra. Un MIDI de
    division 480 y un chart de 192 miden el mismo compas con numeros distintos.
    """
    try:
        division, tracks = smf.parse(midi.read_bytes())
    except smf.MidiReadError as exc:
        raise PipelineError(f"{midi.name}: {exc}") from None

    raw_tempos = sorted({t for track in tracks for t in track.tempos})
    raw_sigs = sorted({s for track in tracks for s in track.time_signatures})
    if not raw_tempos:
        raw_tempos = [(0, 120.0)]
    if raw_tempos[0][0] != 0:
        # Sin evento en el tick 0 el mapa no arranca. Se extiende el primero
        # hacia atras, que es lo que hace cualquier secuenciador al leerlo.
        raw_tempos.insert(0, (0, raw_tempos[0][1]))

    meter = 4
    if raw_sigs and raw_sigs[0][0] == 0:
        meter = max(1, int(raw_sigs[0][1]))

    def scale(tick: int) -> int:
        return round(tick * resolution / division)

    song_bpm = quantize_bpm(raw_tempos[0][1])
    ticks_per_measure = resolution * meter
    seconds_per_measure = meter * 60.0 / song_bpm
    n_measures = max(1, round(pad_seconds / seconds_per_measure))
    origin_tick = n_measures * ticks_per_measure
    lead_bpm = quantize_bpm((origin_tick / resolution) * 60.0 / pad_seconds)

    tempos = [TempoEvent(0, lead_bpm)]
    tempos += [TempoEvent(origin_tick + scale(tick), bpm)
               for tick, bpm in raw_tempos]

    # El denominador NO se valida confiando en `TimeSignature`, aunque rechace lo
    # que no sea potencia de 2: `smf.parse` lo devuelve ya como `2 ** exponente`,
    # asi que siempre lo es y esa comprobacion nunca salta. Un archivo corrupto con
    # exponente 200 colaba un 2**200 perfectamente "valido" que dejaba
    # `ticks_per_measure` en cero y escribia `TS n 200` en el .chart.
    signatures = [TimeSignature(0, meter, 4)]
    for tick, numerator, denominator in raw_sigs:
        if not 1 <= denominator <= 64:
            continue
        try:
            signatures.append(TimeSignature(origin_tick + scale(tick),
                                            max(1, int(numerator)),
                                            int(denominator)))
        except ValueError:
            # Denominador que no es potencia de 2. Se ignora ese cambio en vez
            # de tumbar la generacion entera por un compas raro.
            continue

    tempo_map = TempoMap(tempos, signatures, resolution)
    note = (f"del propio MIDI: division {division}, {len(raw_tempos)} eventos de "
            f"tempo, {song_bpm:g} BPM inicial, compas {meter}/4")
    return tempo_map, origin_tick, note


# --------------------------------------------------------------------------
# Bateria: percusion General MIDI -> carriles
# --------------------------------------------------------------------------

def _snap_hits(notes, tempo_map: TempoMap, origin_tick: int, step: int,
               tolerance: float) -> tuple[dict[int, dict[int, bool]], int]:
    """Cuantiza golpes a una rejilla. Devuelve (tick -> carriles, descartados).

    Dos golpes que caen en el mismo tick son un acorde de bateria, no dos eventos
    seguidos: por eso la clave es el tick y el valor un conjunto de carriles.
    """
    # Nunca se acepta mas de un 40% del paso, igual que `grid_share`. Sin el tope
    # el presupuesto de perdida deja de discriminar: con 45 ms de tolerancia,
    # cualquier rejilla de paso menor que 90 ms acepta TODOS los golpes esten
    # donde esten, asi que `_drums_subdivision` veria cero descartes en una
    # rejilla que no restringe nada y la elegiria.
    step_seconds = step * 60.0 / (tempo_map.bpm_at_tick(origin_tick)
                                  * tempo_map.resolution)
    limit = min(tolerance, 0.4 * step_seconds)

    hits: dict[int, dict[int, bool]] = {}
    dropped = 0
    for note in notes:
        tick, distance = tx_frontend.nearest_grid_tick(
            note.start + PAD_SECONDS, tempo_map, origin_tick, step)
        if distance > limit:
            dropped += 1
            continue
        pad = drums_mod.to_pad(round(note.pitch))
        lanes = hits.setdefault(tick, {})
        # Si el mismo carril suena como tom y como platillo, gana el platillo.
        lanes[pad.lane] = lanes.get(pad.lane, False) or pad.cymbal
    return hits, dropped


def _drums_subdivision(notes, times: list[float], tempo_map: TempoMap,
                       origin_tick: int, tolerance: float) -> int:
    """La rejilla mas gruesa que pierda menos de `DRUMS_DROP_BUDGET` golpes.

    Arranca donde diria `choose_subdivision` y afina desde ahi. Nunca elige una
    rejilla mas gruesa que esa: el criterio de origen ya evita el otro extremo,
    que es cuantizar percusion a una rejilla tan fina que deja de restringir.
    """
    start, _ = tx_frontend.choose_subdivision(times, tempo_map, origin_tick,
                                              tolerance=tolerance)
    finer = [s for s in tx_frontend.SUBDIVISIONS if s >= start] or [start]

    budget = DRUMS_DROP_BUDGET * len(notes)
    scores = []
    for candidate in finer:
        _, dropped = _snap_hits(notes, tempo_map, origin_tick,
                                RESOLUTION // candidate, tolerance)
        if dropped <= budget:
            return candidate
        scores.append((dropped, candidate))

    # Ninguna cumple el presupuesto. Afinar mas NO garantiza perder menos: al
    # topar la ventana al 40% del paso, una rejilla mas fina tiene una ventana mas
    # estrecha, asi que los descartes pueden subir. Se coge la que menos pierda, y
    # a igualdad la mas gruesa, que es la mas legible en Moonscraper.
    return min(scores)[1]


def drums_track(notes, tempo_map: TempoMap, origin_tick: int,
                subdivision: int | None, tolerance: float,
                difficulty: str = "Expert") -> tuple[Track, str]:
    """Golpes de percusion -> pista de bateria, sin sustains ni flags.

    La bateria no pasa por `charting` porque no le hace falta: en la coleccion de
    referencia los charts de bateria no llevan NINGUN sustain y hay un solo flag
    de force en total. Solo hay que cuantizar, agrupar los golpes simultaneos y
    decidir el carril, que es lo que sabe `drums.to_pad`.
    """
    if not notes:
        raise PipelineError("La pista de bateria no trae ningun golpe.")

    times = [n.start + PAD_SECONDS for n in notes]
    if subdivision is None:
        subdivision = _drums_subdivision(notes, times, tempo_map, origin_tick,
                                         tolerance)
    hits, dropped = _snap_hits(notes, tempo_map, origin_tick,
                               RESOLUTION // subdivision, tolerance)

    track = Track(instrument=DRUMS, difficulty=difficulty)
    for tick in sorted(hits):
        lanes = hits[tick]
        chosen = sorted(lanes)
        if len(chosen) > MAX_SIMULTANEOUS:
            # Se conserva el bombo y los golpes mas graves, que son el pulso.
            chosen = [drums_mod.KICK] if drums_mod.KICK in lanes else []
            rest = [lane for lane in sorted(lanes) if lane != drums_mod.KICK]
            chosen += rest[:MAX_SIMULTANEOUS - len(chosen)]
            chosen.sort()
        for lane in chosen:
            track.notes.append(Note(tick=tick, fret=lane, sustain=0,
                                    cymbal=lanes[lane]))

    report = (f"rejilla {tx_frontend.label(subdivision)} | {len(hits)} eventos, "
              f"{len(track.notes)} notas")
    if dropped:
        report += (f" | {dropped} de {len(notes)} golpes fuera de rejilla "
                   f"({dropped / len(notes):.0%}) descartados")
    return track, report


# --------------------------------------------------------------------------
# Preparacion: lo que se hace UNA vez por cancion
# --------------------------------------------------------------------------

@dataclass
class Options:
    """Todo lo que no depende de la asignacion de canales."""

    detector: str = "auto"
    bpm_hint: float | None = None
    max_error: float = tempo_fit.DEFAULT_MAX_ERROR
    subdivision: int | None = None
    tolerance: float = tx_frontend.DEFAULT_TOLERANCE
    cleanup: bool = True
    star_power: bool = True
    formats: tuple[str, ...] = ("chart",)
    detect_sections: bool = False
    lyrics: Path | None = None
    lyrics_mode: str = "line"
    lang: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    year: str | None = None
    charter: str = "chartgen/midi"
    lookup_metadata: bool = True
    fetch_art: bool = True
    art: Path | None = None
    find_preview: bool = True
    overwrite: bool = False


@dataclass
class Prepared:
    """Lo caro, ya resuelto. Se reutiliza para todas las variantes."""

    midi: Path
    audio: Path | None
    tracks: list  # list[midi_frontend.SourceTrack]
    tempo_map: TempoMap
    origin_tick: int
    tempo_note: str
    tempo_reliable: bool
    metadata: Metadata
    art: Path | None
    preview_start: float
    duration: float
    options: Options
    section_events: list = field(default_factory=list)
    lyric_events: list = field(default_factory=list)

    def notes_of(self, track_name: str):
        for track in self.tracks:
            if track.name == track_name:
                return track.notes
        raise PipelineError(f"El MIDI no tiene una pista llamada '{track_name}'.")


def prepare(midi: Path, audio: Path | None = None, options: Options | None = None,
            log=print) -> Prepared:
    """Lee el MIDI, fija el tempo y resuelve metadatos. Una vez por cancion."""
    options = options or Options()
    midi = Path(midi)
    if not midi.exists():
        raise PipelineError(f"No existe el MIDI: {midi}")

    if audio is None:
        audio = find_audio(midi)
    if audio is not None and not Path(audio).exists():
        raise PipelineError(f"No existe el audio: {audio}")
    audio = Path(audio) if audio else None

    # --- Pistas del MIDI -------------------------------------------------
    tracks = midi_frontend.read_tracks(midi)
    if not tracks:
        raise PipelineError(f"{midi.name} no tiene ninguna pista con notas.")

    # --- Tempo -----------------------------------------------------------
    reliable = True
    if audio is not None:
        log(f"Analizando el tempo de {audio.name}...")
        fit = tempo_from_audio(audio, options.detector, options.bpm_hint,
                               options.max_error, log=log)
        tempo_map, origin_tick = fit.tempo_map, fit.origin_tick
        tempo_note = fit.describe()
        reliable = fit.is_reliable
    else:
        log("No hay audio al lado del MIDI: el tempo se toma del MIDI.")
        tempo_map, origin_tick, tempo_note = tempo_from_midi(midi)

    # --- Metadatos -------------------------------------------------------
    stem = (audio or midi).stem
    guessed_artist, guessed_title = meta_mod.from_filename(stem)
    metadata, info = resolve_metadata(options, guessed_artist, guessed_title,
                                      stem, log=log)

    art = Path(options.art) if options.art else None
    if art is None and info is not None and options.fetch_art:
        try:
            image = meta_mod.fetch_cover(
                info.release_mbid, release_group_mbid=info.release_group_mbid)
            if image:
                art = midi.parent / "_cover.jpg"
                art.write_bytes(image)
        except meta_mod.MetadataError as exc:
            log(f"  aviso: {exc}")

    # --- Preview y duracion ----------------------------------------------
    preview_start = PAD_SECONDS
    duration = 0.0
    if audio is not None:
        try:
            duration = audio_mod.probe_duration(audio)
            if options.find_preview:
                preview_start = audio_mod.find_preview_start(audio) + PAD_SECONDS
        except audio_mod.AudioError as exc:
            log(f"  aviso: no se pudo medir el audio ({exc}).")

    prepared = Prepared(
        midi=midi, audio=audio, tracks=tracks, tempo_map=tempo_map,
        origin_tick=origin_tick, tempo_note=tempo_note, tempo_reliable=reliable,
        metadata=metadata, art=art, preview_start=preview_start,
        duration=duration, options=options,
    )

    # --- Secciones y letra: dependen del tempo, no de la asignacion -------
    if options.detect_sections and audio is not None:
        log("Detectando secciones del audio...")
        found = sections_mod.detect(audio)
        prepared.section_events = sections_mod.to_events(
            found, tempo_map, pad_seconds=PAD_SECONDS, origin_tick=origin_tick)
        log(f"  {len(found)} secciones (limites fiables, nombres heuristicos)")
    elif options.detect_sections:
        log("  sin audio no se pueden detectar secciones; se omiten.")

    if options.lyrics:
        lines = lyrics_mod.parse_lrc(Path(options.lyrics))
        prepared.lyric_events = lyrics_mod.to_events(
            lines, tempo_map, pad_seconds=PAD_SECONDS,
            mode=options.lyrics_mode, lang=options.lang)
        log(f"  letra: {len(lines)} versos -> {len(prepared.lyric_events)} eventos")

    return prepared


def resolve_metadata(options: Options, fallback_artist: str,
                     fallback_title: str, stem: str, log=print):
    """Lo explicito manda sobre MusicBrainz, y MusicBrainz sobre el nombre del archivo."""
    title = options.title or fallback_title or stem
    artist = options.artist or fallback_artist

    info = None
    if options.lookup_metadata and artist and title:
        log("Buscando metadatos...")
        try:
            client = meta_mod.MusicBrainz(cache_dir=CACHE_DIR)
            info = client.lookup(artist, title, album=options.album)
        except meta_mod.MetadataError as exc:
            log(f"  aviso: MusicBrainz fallo ({exc}); se sigue sin metadatos.")

    if info is not None and not meta_mod.same_artist(artist, info.artist):
        log(f"  descartado: se busco '{artist}' y MusicBrainz devolvio "
            f"'{info.artist}'.")
        info = None

    if info is None:
        return Metadata(name=title, artist=artist or "chartgen",
                        album=options.album or "", genre=options.genre or "",
                        year=options.year or "", charter=options.charter), None

    log(f"  [{info.confidence}] {info.describe()}")
    if info.confidence != "high":
        log("  aviso: busqueda sin album; puede ser un directo o un "
            "recopilatorio.")
    return Metadata(
        name=title,
        artist=options.artist or info.artist or artist,
        album=options.album or info.album,
        genre=options.genre or info.genre,
        year=options.year or info.year,
        charter=options.charter,
        album_track=info.track_number,
    ), info


# --------------------------------------------------------------------------
# Construccion: lo que se hace UNA vez por variante
# --------------------------------------------------------------------------

@dataclass
class VariantResult:
    folder: Path
    reports: dict[str, str]
    warnings: list[str]
    song: Song


def build_song(prepared: Prepared, assignment: dict[str, list[str]],
               log=print) -> tuple[Song, dict[str, str], list[str]]:
    """Aplica una asignacion canal->pistas y devuelve el Song listo para empaquetar.

    `assignment` va de CANAL del chart a la lista de pistas del MIDI que van
    dentro. Varias pistas en un canal se suman, que es lo que se quiere al juntar
    por ejemplo dos teclados en `keys`; y la misma pista puede aparecer en dos
    canales, que es lo que se quiere para dos jugadores tocando la misma linea.
    """
    options = prepared.options
    by_channel: dict[str, list] = {}
    for channel, track_names in assignment.items():
        if channel not in CHANNELS:
            raise PipelineError(
                f"Canal desconocido: '{channel}'. Hay: {', '.join(CHANNELS)}.")
        if not track_names:
            # Un canal sin pistas no se escribe, y no es un error: es como la
            # combinatoria representa "este canal va vacio en esta variante".
            continue
        notes: list = []
        for track_name in track_names:
            track_notes = prepared.notes_of(track_name)
            if not track_notes:
                # Nombrar la pista: antes se descartaba en silencio y el error que
                # acababa saliendo era "la asignacion esta vacia", que manda a
                # buscar el problema al sitio equivocado.
                raise PipelineError(
                    f"La pista '{track_name}' no tiene notas, asi que el canal "
                    f"'{channel}' saldria vacio.")
            notes.extend(track_notes)
        by_channel[channel] = notes
    if not by_channel:
        raise PipelineError("La asignacion esta vacia: ninguna pista va a un canal.")

    song = Song(metadata=Metadata(name="", artist=""),
                tempo_map=prepared.tempo_map, origin_tick=prepared.origin_tick)
    reports: dict[str, str] = {}
    warnings: list[str] = []

    for channel in CHANNELS:
        notes = by_channel.get(channel)
        if not notes:
            continue
        notes = sorted(notes, key=lambda n: n.start)

        if channel == DRUMS:
            track, report = drums_track(notes, prepared.tempo_map,
                                        prepared.origin_tick,
                                        options.subdivision, options.tolerance)
            song.add_track(track)
            if options.star_power:
                add_star_power(track, prepared.tempo_map, prepared.origin_tick)
            reports[channel] = report
            continue

        part, quant = tx_frontend.convert(
            notes, prepared.tempo_map, prepared.origin_tick, instrument=channel,
            star_power=options.star_power, subdivision=options.subdivision,
            tolerance=options.tolerance, cleanup=options.cleanup)
        for track in part.tracks:
            song.add_track(track)
        reports[channel] = quant.describe()
        warnings += [f"{channel}: {w}" for w in quant.warnings()]

    song.events.extend(prepared.section_events)
    song.events.extend(prepared.lyric_events)

    # Clone Hero abre la cancion en la pista de guitarra: sin ella se ve vacia.
    present = {t.instrument for t in song.tracks}
    if "guitar" not in present:
        warnings.append(
            "no hay canal de guitarra: en el juego hay que cambiar de "
            f"instrumento a {sorted(present)[0]} o la cancion se vera vacia")
    return song, reports, warnings


def build_variant(prepared: Prepared, assignment: dict[str, list[str]],
                  out_root: Path, title_suffix: str = "",
                  log=print) -> VariantResult:
    """Escribe una carpeta lista para copiar a la biblioteca de Clone Hero."""
    from dataclasses import replace as dc_replace

    song, reports, warnings = build_song(prepared, assignment, log=log)

    # El sufijo va en el TITULO, no solo en el nombre de carpeta: si no, cinco
    # variantes de la misma cancion salen indistinguibles en la lista del juego.
    metadata = dc_replace(prepared.metadata)
    if title_suffix:
        metadata.name = f"{metadata.name} {title_suffix}"
    metadata.preview_start = prepared.preview_start
    # Solo los canales que de verdad tienen pistas: marcar una dificultad de un
    # canal vacio lo hace aparecer seleccionable en el juego y suena a nada.
    for track in song.tracks:
        if hasattr(metadata, f"diff_{track.instrument}"):
            setattr(metadata, f"diff_{track.instrument}", 3)
    song.metadata = metadata

    result = package.build(song, out_root, audio_src=prepared.audio,
                           art_src=prepared.art,
                           overwrite=prepared.options.overwrite,
                           formats=prepared.options.formats)
    return VariantResult(folder=Path(result.folder), reports=reports,
                         warnings=warnings, song=song)


def density_lines(song: Song, seconds: float) -> list[str]:
    """Densidad de cada pista frente a la de los charts humanos."""
    if seconds <= 0:
        return ["  (sin audio no se puede medir la densidad)"]
    out = []
    for track in song.tracks:
        _, verdict, _ = transcribe_mod.density_verdict(
            len({n.tick for n in track.notes}), seconds, track.instrument)
        out.append(f"  {track.instrument:<7} {verdict}")
    return out
