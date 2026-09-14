"""Metadatos desde MusicBrainz y caratula desde Cover Art Archive.

Ninguna de las dos APIs necesita clave. MusicBrainz si exige un User-Agent que
identifique la aplicacion y limita a una peticion por segundo (`musicbrainzngs`
lo respeta solo). Cover Art Archive redirige a nodos de almacenamiento de
archive.org que fallan de vez en cuando, de ahi los reintentos.

## Por que hay dos rutas de busqueda

Buscar solo por artista y titulo es POCO FIABLE, y conviene saberlo. MusicBrainz
modela cada master como una grabacion distinta, y para artistas con muchos
directos y bootlegs la busqueda de grabaciones devuelve decenas de ediciones en
vivo sin que la de estudio aparezca siquiera. Medido con "T.N.T." de AC/DC: 92
releases en los resultados, CERO de estudio. Ni subir el limite, ni ordenar por
tipo, ni filtrar con Lucene en el servidor (`-secondarytype:*` anula la consulta
entera, `recording:` no existe en el indice de releases) lo arreglan.

Con el album conocido, en cambio, la busqueda de releases acierta de lleno: en
AC/DC, Rush, Metallica y Nirvana devolvio el album correcto y el año de la
edicion original (1975, 1981, 1986, 1991).

Por eso `lookup()` usa la ruta de album cuando se le da uno, y marca el
resultado como de baja confianza cuando ha tenido que adivinar.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_CONTACT = "https://github.com/local/chartgen"
"""Contacto para el User-Agent de MusicBrainz. Configurable con CHARTGEN_CONTACT.

Su politica pide poder contactar al operador si una app abusa del servicio. Se
deja un placeholder a proposito: no se manda el correo del usuario a un servicio
externo sin que lo pida explicitamente.
"""

_NON_STUDIO_HINTS = ("live", "remix", "demo", "instrumental", "karaoke",
                     "edit", "rehearsal", "acoustic version")


class MetadataError(RuntimeError):
    pass


@dataclass
class ReleaseInfo:
    title: str
    artist: str
    album: str = ""
    year: str = ""
    genre: str = ""
    track_number: int = 0
    release_mbid: str = ""
    release_group_mbid: str = ""
    recording_mbid: str = ""
    confidence: str = "low"
    """'high' solo por la ruta de album. Sin album se esta adivinando."""
    candidates: list[str] = field(default_factory=list)

    def describe(self) -> str:
        parts = [f"{self.artist} - {self.title}"]
        if self.album:
            year = f" ({self.year})" if self.year else ""
            parts.append(f"album: {self.album}{year}")
        if self.genre:
            parts.append(f"genero: {self.genre}")
        if self.track_number:
            parts.append(f"pista {self.track_number}")
        return " | ".join(parts)


def _contact() -> str:
    return os.environ.get("CHARTGEN_CONTACT", DEFAULT_CONTACT)


def _norm(text: str) -> str:
    return "".join(c.lower() for c in text if c.isalnum())


def is_studio(release: dict) -> bool:
    """True si la edicion no tiene tipos secundarios (live, compilation, ...)."""
    group = release.get("release-group", {}) or {}
    return not group.get("secondary-type-list")


def looks_live(recording: dict) -> bool:
    """MusicBrainz marca las tomas alternativas en `disambiguation`.

    Es la señal mas fiable que da la API: 'live, 1983: USA', 'remix', 'demo'.
    Una grabacion de estudio suele traer el campo vacio.
    """
    text = (recording.get("disambiguation") or "").lower()
    return any(hint in text for hint in _NON_STUDIO_HINTS)


def release_sort_key(release: dict) -> tuple:
    """Ordena ediciones: estudio primero, luego la mas antigua."""
    date = release.get("date", "") or (
        release.get("release-group", {}) or {}).get("first-release-date", "")
    year = int(date[:4]) if date[:4].isdigit() else 9999
    return (0 if is_studio(release) else 1, year)


class MusicBrainz:
    def __init__(self, contact: str | None = None,
                 cache_dir: Path | None = None) -> None:
        import musicbrainzngs

        from . import __version__

        self.mb = musicbrainzngs
        self.mb.set_useragent("chartgen", __version__, contact or _contact())
        self.mb.set_rate_limit(True)  # 1 req/s, exigido por su politica
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- cache -------------------------------------------------------------
    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:120]
        return self.cache_dir / f"{safe}.json"

    def _query(self, key: str, call):
        cached = self._cached(key)
        if cached is not None:
            return cached
        try:
            data = call()
        except Exception as exc:
            raise MetadataError(f"MusicBrainz no respondio: {exc}") from exc
        self._store(key, data)
        return data

    def _cached(self, key: str):
        path = self._cache_path(key)
        if path and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def _store(self, key: str, value) -> None:
        path = self._cache_path(key)
        if path:
            path.write_text(json.dumps(value), encoding="utf-8")

    # -- busqueda ----------------------------------------------------------
    def lookup(self, artist: str, title: str,
               album: str | None = None) -> ReleaseInfo | None:
        info = (self._by_album(artist, title, album) if album
                else self._by_recording(artist, title))
        if info and info.release_mbid:
            self._enrich(info)
        return info

    def _by_album(self, artist: str, title: str, album: str) -> ReleaseInfo | None:
        """Ruta fiable: con el album conocido la busqueda de releases acierta."""
        data = self._query(
            f"rel_{artist}_{album}",
            lambda: self.mb.search_releases(artist=artist, release=album, limit=25),
        )
        releases = [r for r in data.get("release-list", []) if is_studio(r)]
        if not releases:
            releases = data.get("release-list", [])
        if not releases:
            return None

        releases.sort(key=release_sort_key)
        best = releases[0]
        date = best.get("date", "")
        credit = best.get("artist-credit", [{}])[0].get("artist", {})

        return ReleaseInfo(
            title=title,
            artist=credit.get("name", "") or artist,
            album=best.get("title", album),
            year=date[:4] if date[:4].isdigit() else "",
            release_mbid=best.get("id", ""),
            confidence="high",
            candidates=[
                f"{r.get('title', '?')} ({r.get('date', '')[:4] or '????'})"
                for r in releases[:6]
            ],
        )

    def _by_recording(self, artist: str, title: str,
                      limit: int = 25) -> ReleaseInfo | None:
        """Ruta de respaldo, sin album. Poco fiable: ver el docstring del modulo."""
        data = self._query(
            f"rec_{artist}_{title}",
            lambda: self.mb.search_recordings(
                recording=title, artist=artist, limit=limit),
        )
        return self._info_from_recordings(
            data.get("recording-list", []), artist, title)

    @staticmethod
    def _pick_recording(recordings: list, title: str):
        """Elige el par (grabacion, edicion) mejor situado. Funcion pura.

        Separada de la llamada de red a proposito: la eleccion es la parte con
        reglas y merece tests, la peticion HTTP no.
        """
        pairs = []
        for rec in recordings:
            if _norm(rec.get("title", "")) != _norm(title):
                continue
            live = looks_live(rec)
            for rel in rec.get("release-list", []) or []:
                pairs.append(((1 if live else 0,) + release_sort_key(rel), rec, rel))

        if not pairs:
            return None, []

        pairs.sort(key=lambda p: p[0])
        candidates = [
            f"{rel.get('title', '?')} ({rel.get('date', '')[:4] or '????'})"
            for _, _, rel in pairs[:6]
        ]
        key, rec, rel = pairs[0]
        return (rec, rel, key), candidates

    @classmethod
    def _info_from_recordings(cls, recordings: list, artist: str,
                              title: str) -> ReleaseInfo | None:
        picked, candidates = cls._pick_recording(recordings, title)
        if picked is None:
            return None

        rec, rel, key = picked
        credit = rec.get("artist-credit", [{}])[0].get("artist", {})
        date = rel.get("date", "") or (
            rel.get("release-group", {}) or {}).get("first-release-date", "")

        # Sin album NUNCA se declara alta confianza. Medido: para "T.N.T." de
        # AC/DC esta ruta elige un bootleg cuyo release-group esta mal etiquetado
        # en MusicBrainz y pasa los filtros de tipo. Sin album se esta adivinando
        # y el usuario merece saberlo.
        studio = key[0] == 0 and key[1] == 0
        return ReleaseInfo(
            title=rec.get("title", title),
            artist=credit.get("name", "") or artist,
            album=rel.get("title", ""),
            year=date[:4] if date[:4].isdigit() else "",
            release_mbid=rel.get("id", ""),
            recording_mbid=rec.get("id", ""),
            confidence="medium" if studio else "low",
            candidates=candidates,
        )

    def _enrich(self, info: ReleaseInfo) -> None:
        """Genero y numero de pista: no vienen en la respuesta de busqueda."""
        try:
            data = self._query(
                f"relfull_{info.release_mbid}",
                lambda: self.mb.get_release_by_id(
                    info.release_mbid,
                    includes=["release-groups", "tags", "recordings"]),
            )
        except MetadataError:
            return

        release = data.get("release", {})
        group = release.get("release-group", {}) or {}
        info.release_group_mbid = group.get("id", "")
        tags = (release.get("tag-list")
                or (release.get("release-group", {}) or {}).get("tag-list") or [])
        if tags:
            best = max(tags, key=lambda t: int(t.get("count", 0)))
            info.genre = best.get("name", "").title()

        if not info.track_number:
            info.track_number = self._find_track(release, info.title)

    @staticmethod
    def _find_track(release: dict, title: str) -> int:
        for medium in release.get("medium-list", []):
            for entry in medium.get("track-list", []):
                name = entry.get("recording", {}).get("title", "") or entry.get(
                    "title", "")
                if _norm(name) == _norm(title):
                    number = entry.get("number", "")
                    if number.isdigit():
                        return int(number)
        return 0


def fetch_cover(release_mbid: str, size: int = 500, retries: int = 4,
                timeout: int = 25, release_group_mbid: str = "") -> bytes | None:
    """Descarga la portada de Cover Art Archive. None si no hay ninguna.

    Prueba primero la edicion concreta y despues el release-group. Muchas
    ediciones individuales no tienen portada subida aunque el album si: en la
    prueba con cuatro discos conocidos, dos fallaban por release y aparecian por
    release-group.

    Reintenta porque CAA redirige a nodos de archive.org cuyo DNS falla de forma
    intermitente: en pruebas, el mismo MBID fallo y al segundo intento devolvio
    200 sin cambiar nada.
    """
    import requests

    from . import __version__

    urls = []
    if release_mbid:
        urls.append(f"https://coverartarchive.org/release/{release_mbid}/front-{size}")
    if release_group_mbid:
        urls.append(
            f"https://coverartarchive.org/release-group/{release_group_mbid}"
            f"/front-{size}")
    if not urls:
        return None

    session = requests.Session()
    session.headers["User-Agent"] = f"chartgen/{__version__} ({_contact()})"

    last: Exception | None = None
    for url in urls:
        for attempt in range(retries):
            try:
                response = session.get(url, timeout=timeout, allow_redirects=True)
                if response.status_code == 404:
                    break  # esta ruta no tiene portada; probar la siguiente
                response.raise_for_status()
                return response.content
            except Exception as exc:
                last = exc
                if attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
    if last is not None:
        raise MetadataError(f"Cover Art Archive fallo: {last}")
    return None


def to_dict(info: ReleaseInfo) -> dict:
    return asdict(info)

# --------------------------------------------------------------------------
# Deducir artista y titulo del nombre del archivo
# --------------------------------------------------------------------------

_CHARTER_TAG = re.compile(r"\s*\[[^\]]*\]\s*$")
"""Etiqueta de charter al final: `[LMBECIL1832]`. Se quita del titulo.

Se quita SOLO la ultima. Hay titulos con dos grupos de corchetes, y el de dentro
es contenido: "Email Me (Senor Internet) [feat. Sarita] [LMBECIL1832]" tiene que
quedarse el feat. y perder la firma."""

_LOOSE_SEPARATOR = re.compile(r"\s+-\s*")
"""Guion con espacio delante pero quiza no detras: `Artista -Titulo`.

Solo se usa si no hay un ` - ` limpio. Exigir el espacio DELANTE es lo que
distingue este caso de un titulo con guion pegado como "Tick-Tack", que no
lleva artista y no debe partirse."""

_LEADING_TRACK = re.compile(r"^\s*\d{1,3}\s*[.\-_)]\s+")
"""Numero de pista al principio: `03. `, `07 - `. No aparece en la coleccion de
referencia, pero si en cualquier carpeta ripeada de un disco."""


def same_artist(a: str, b: str) -> bool:
    """Compara dos nombres de artista ignorando mayusculas, tildes y puntuacion.

    Existe porque MusicBrainz responde a una busqueda de artista con OTRO
    artista. Buscando "SANTOS BRAVOS / VELOCIDADE" devolvio "Nenhum De Nos", un
    grupo brasileno distinto, con confianza media: la cancion se llama igual y el
    indice de grabaciones no distingue. Ya esta documentado arriba que la
    busqueda sin album es poco fiable; esto es la comprobacion que lo aprovecha.
    """
    def normalize(value: str) -> str:
        plain = unicodedata.normalize("NFKD", value or "")
        plain = "".join(c for c in plain if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]", "", plain.casefold())

    left, right = normalize(a), normalize(b)
    if not left or not right:
        return True          # sin nada que comparar no se contradice a nadie
    return left == right or left in right or right in left


def from_filename(stem: str) -> tuple[str, str]:
    """`"Artista - Titulo.mp3"` -> `("Artista", "Titulo")`.

    Calibrado contra los nombres de las 203 carpetas de la coleccion de
    referencia, que es la misma convencion con la que la gente nombra los mp3:

    | | carpetas |
    |---|---|
    | con ` - ` | 200 de 203 |
    | con exactamente un ` - ` | 196 |
    | con dos | 4 |
    | sin ninguno | 3 |
    | con `[corchetes]` | 25 |
    | con `(parentesis)` | 24 |

    De ahi salen las tres reglas:

    - Se parte por el PRIMER ` - `. Con dos separadores, el segundo es parte del
      titulo: "Jenni Rivera - A Cambio De Que - Banda" es un titulo con guion, no
      un artista llamado "Jenni Rivera - A Cambio De Que".
    - Los `[corchetes]` finales se quitan: son la firma del charter.
    - Los `(parentesis)` se conservan: son parte del titulo, casi siempre un feat.

    Sin separador devuelve `("", stem)`: hay titulo pero no de quien es, y quien
    llame decide si lo pregunta o lo deja en blanco.
    """
    stem = _LEADING_TRACK.sub("", stem.strip())
    artist, separator, title = stem.partition(" - ")
    if not separator:
        # Una de las 203 carpetas escribe "Artista -Titulo", sin espacio detras.
        loose = _LOOSE_SEPARATOR.split(stem, maxsplit=1)
        if len(loose) == 2 and all(part.strip() for part in loose):
            artist, title = loose
        else:
            return "", _CHARTER_TAG.sub("", stem).strip()
    return artist.strip(), _CHARTER_TAG.sub("", title).strip()
