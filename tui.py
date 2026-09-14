"""Interfaz de consola: elegir que pista del MIDI va a que canal del chart.

## Por que no es un .bat

Lo que hay que hacer aqui es: mostrar una lista de longitud desconocida, dejar
elegir varios elementos por canal, multiplicar las elecciones y confirmar. En cmd
eso significa variables numeradas a mano (`PISTA1`, `PISTA2`...), `for /f` sobre
la salida de otro proceso y `enabledelayedexpansion` en bucles anidados, y cada
una de esas tres cosas tiene una forma distinta de romperse en silencio. El .bat
se queda en lo que hace bien: recibir el arrastre y llamar aqui.

## Por que los prompts son numeros y no nombres

Los nombres de pista de un transcriptor traen guiones bajos, acentos y a veces
caracteres que la consola en cp1252 no sabe ni imprimir. Escribirlos a mano en un
prompt es una fuente de errores gratuita, asi que se elige por indice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pipeline
import variants as variants_mod

BAR = "=" * 68


class Aborted(RuntimeError):
    """El usuario se salio. No es un error que haya que rastrear."""


def say(text: str = "") -> None:
    """print() tolerante con la consola de Windows en cp1252."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding))


def ask(prompt: str, default: str = "") -> str:
    """Lee una linea. Sin terminal (stdin cerrado) se aborta en vez de colgarse."""
    try:
        answer = input(prompt).strip()
    except EOFError:
        raise Aborted("no hay terminal para preguntar: usa las opciones de la "
                      "linea de comandos.") from None
    except KeyboardInterrupt:
        raise Aborted("cancelado.") from None
    return answer or default


def ask_indices(prompt: str, valid: int, allow_empty: bool = True) -> list[int]:
    """Lee '1,3,5' y devuelve [0,2,4]. Reintenta mientras no sea valido."""
    while True:
        raw = ask(prompt)
        if not raw:
            if allow_empty:
                return []
            say("  Escribe al menos un numero.")
            continue
        try:
            picked = [int(p) for p in raw.replace(" ", ",").split(",") if p]
        except ValueError:
            say(f"  Solo numeros separados por comas (1-{valid}).")
            continue
        bad = [p for p in picked if not 1 <= p <= valid]
        if bad:
            say(f"  Fuera de rango: {', '.join(str(b) for b in bad)}. "
                f"Validos 1-{valid}.")
            continue
        # Se conserva el orden en que los escribio y se quitan repetidos.
        seen, out = set(), []
        for p in picked:
            if p not in seen:
                seen.add(p)
                out.append(p - 1)
        return out


# --------------------------------------------------------------------------
# Pantallas
# --------------------------------------------------------------------------

def show_audio(prepared_midi: Path, audio: Path | None) -> None:
    """Dice si hay audio y, si no, que significa exactamente."""
    if audio is not None:
        say(f"Audio: {audio.name}")
        say("  El mapa de tempo se ajusta a esta grabacion.")
        return

    say("Audio: NO ENCONTRADO al lado del MIDI.")
    say("  Se puede seguir, pero conviene saber que implica:")
    say("    - el tempo se toma del propio MIDI, no de una grabacion;")
    say("    - song.ogg sale en SILENCIO, asi que el chart se podra editar en")
    say("      Moonscraper pero no jugar con musica.")
    say(f"  Para tener las dos cosas, pon un audio junto al MIDI con el mismo")
    say(f"  nombre y vuelve a arrastrarlo:")
    for ext in pipeline.AUDIO_EXTENSIONS[:4]:
        say(f"    {prepared_midi.with_suffix(ext).name}")


def show_tracks(tracks) -> None:
    say()
    say("Pistas del MIDI:")
    for index, track in enumerate(tracks, 1):
        if track.instrument in pipeline.CHANNELS:
            hint = f"se deduce: {track.instrument}"
        elif track.instrument:
            hint = f"se deduce: {track.instrument} (ningun backend lo escribe)"
        else:
            hint = "sin clasificar"
        say(f"  {index:>2}  {track.name[:26]:<28} {len(track.notes):>5} notas   {hint}")


def describe_deduced(deduced: dict[str, str]) -> str:
    """'keys=electric_piano+synth_pad', no 'keys=x, keys=y'.

    `deduced` va de pista a canal, y dos pistas pueden compartir canal. Formatearlo
    sin agrupar hacia parecer que el canal aparecia dos veces, cuando lo que pasa
    es que las dos pistas se suman en uno.
    """
    grouped: dict[str, list[str]] = {}
    for track, channel in deduced.items():
        grouped.setdefault(channel, []).append(track)
    return ", ".join(f"{channel}={'+'.join(tracks)}"
                     for channel, tracks in sorted(grouped.items()))


def choose_mode(deduced: dict[str, str]) -> str:
    say()
    say("Que quieres generar?")
    if deduced:
        say(f"  [1] La asignacion deducida: {describe_deduced(deduced)}")
    else:
        say("  [1] La asignacion deducida  (NO HAY: ninguna pista se reconocio)")
    say("  [2] Asignar a mano, canal por canal")
    say("  [3] Variantes: varios candidatos por canal, una carpeta por combinacion")
    say("  [s] Salir sin generar nada")
    while True:
        choice = ask("  > ").lower()
        if choice == "s":
            raise Aborted("no se genero nada.")
        if choice in ("1", "2", "3"):
            if choice == "1" and not deduced:
                say("  No hay asignacion deducida. Usa [2] o [3].")
                continue
            return choice
        say("  Escribe 1, 2, 3 o s.")


def ask_manual(tracks) -> dict[str, str]:
    """Asignacion a mano: para cada canal, que pistas van dentro."""
    say()
    say("Para cada canal, escribe los numeros de las pistas que van dentro.")
    say("Enter para dejarlo vacio. Varias pistas en un canal se SUMAN.")
    assignment: dict[str, str] = {}
    for channel in pipeline.CHANNELS:
        picked = ask_indices(f"  {channel:<7} > ", len(tracks))
        for index in picked:
            name = tracks[index].name
            if name in assignment:
                say(f"    aviso: '{name}' ya estaba en {assignment[name]}; "
                    f"se mueve a {channel}.")
            assignment[name] = channel
    if not assignment:
        raise Aborted("no se asigno ninguna pista.")
    return assignment


def ask_choices(tracks) -> dict[str, list[str | None]]:
    """Candidatos por canal. Varios candidatos = varias variantes."""
    say()
    say("Para cada canal, escribe los CANDIDATOS separados por comas.")
    say("Cada combinacion sale como una carpeta aparte.")
    say("  ejemplo:  guitar > 1,3    genera una carpeta con la pista 1 en")
    say("            guitarra y otra con la 3.")
    say("Enter deja el canal fuera. Un 0 entre los candidatos agrega la opcion")
    say("de dejarlo vacio en algunas variantes.")
    choices: dict[str, list[str | None]] = {}
    for channel in pipeline.CHANNELS:
        while True:
            raw = ask(f"  {channel:<7} > ")
            if not raw:
                break
            parts = [p for p in raw.replace(" ", ",").split(",") if p]
            try:
                numbers = [int(p) for p in parts]
            except ValueError:
                say(f"  Solo numeros separados por comas (0-{len(tracks)}).")
                continue
            bad = [n for n in numbers if not 0 <= n <= len(tracks)]
            if bad:
                say(f"  Fuera de rango: {', '.join(str(b) for b in bad)}.")
                continue
            candidates: list[str | None] = []
            for n in numbers:
                value = variants_mod.EMPTY if n == 0 else tracks[n - 1].name
                if value not in candidates:
                    candidates.append(value)
            choices[channel] = candidates
            break
    if not choices:
        raise Aborted("no se eligio ningun candidato.")
    return choices


def confirm_variants(items, cap: int = variants_mod.MAX_VARIANTS) -> list:
    """Muestra lo que se va a generar y pide confirmacion si son muchas."""
    say()
    if not items:
        raise Aborted("la combinatoria no dejo ninguna variante valida. "
                      "Suele pasar por repetir la misma pista en dos canales.")

    say(f"Saldrian {len(items)} carpeta(s):")
    for index, variant in enumerate(items, 1):
        say(f"  {index:>2}  {variant.describe()}")

    if len(items) > cap:
        say()
        say(f"AVISO: son {len(items)}, y cada una lleva su copia del audio.")
        say(f"       Enter genera solo las {cap} primeras; escribe 'todas' para")
        say("       generarlas todas, o un numero para quedarte con las N primeras.")
        answer = ask("  > ").lower()
        if answer == "todas":
            return items
        if answer.isdigit() and int(answer) > 0:
            return items[:int(answer)]
        return items[:cap]

    answer = ask("  Enter para generar, 's' para salir > ").lower()
    if answer == "s":
        raise Aborted("no se genero nada.")
    return items


# --------------------------------------------------------------------------
# Flujo completo
# --------------------------------------------------------------------------

def run(midi: Path, out_root: Path, options: pipeline.Options) -> int:
    say(BAR)
    say(f" ChartScript - {midi.name}")
    say(BAR)

    audio = pipeline.find_audio(midi)
    show_audio(midi, audio)
    if audio is None:
        if ask("  Enter para continuar sin audio, 's' para salir > ").lower() == "s":
            raise Aborted("no se genero nada.")

    # Leer el MIDI antes de lo caro: si no tiene pistas utiles, mejor saberlo ya.
    tracks = pipeline.midi_frontend.read_tracks(midi)
    if not tracks:
        raise pipeline.PipelineError(f"{midi.name} no tiene ninguna pista con notas.")
    show_tracks(tracks)

    deduced = {t.name: t.instrument for t in tracks
               if t.instrument in pipeline.CHANNELS}
    mode = choose_mode(deduced)

    if mode == "1":
        items = [variants_mod.single(deduced)]
    elif mode == "2":
        items = [variants_mod.single(ask_manual(tracks))]
    else:
        choices = ask_choices(tracks)
        expansion = variants_mod.expand(choices)

        # Repetir una pista en dos canales se descarta por defecto porque casi
        # siempre es un accidente de la combinatoria. Pero cuando el usuario ha
        # puesto la misma pista como candidata en dos canales a proposito, callarse
        # la variante que falta es peor que preguntar.
        if expansion.skipped_reuse:
            say()
            say(f"AVISO: {expansion.skipped_reuse} combinacion(es) usan la misma "
                "pista en dos canales")
            say("  y se han descartado. Es legitimo si lo que buscas es la misma")
            say("  linea en dos canales, por ejemplo guitarra y bajo a la vez.")
            if ask("  Enter para dejarlas fuera, 'i' para incluirlas > ").lower() == "i":
                expansion = variants_mod.expand(choices, allow_reuse=True)

        # Solo la nota de duplicados: la de reuso acaba de explicarse arriba con
        # su pregunta, y repetirla aqui era decir lo mismo dos veces seguidas.
        if expansion.skipped_duplicate:
            say(f"  nota: {expansion.skipped_duplicate} combinacion(es) eran "
                "duplicados exactos de otra y se unificaron.")
        items = confirm_variants(expansion.variants)
        varying = variants_mod.varying_channels(choices)

    if mode != "3":
        varying = ()

    # Ahora si: lo caro, una vez para todas las variantes.
    say()
    say(BAR)
    prepared = pipeline.prepare(midi, audio, options, log=say)
    say(f"  tempo: {prepared.tempo_note}")
    if not prepared.tempo_reliable:
        say("  aviso: esta grabacion no se deja modelar bien. La cuantizacion")
        say("         hereda ese error, asi que revisa el resultado.")

    built, failed = [], []
    for index, variant in enumerate(items, 1):
        suffix = variant.label(varying) if len(items) > 1 else ""
        say()
        say(f"[{index}/{len(items)}] {variant.describe()}")
        try:
            result = pipeline.build_variant(prepared, variant.assignment,
                                            out_root, title_suffix=suffix, log=say)
        except (pipeline.PipelineError, pipeline.package.OverwriteError,
                ValueError, OSError) as exc:
            say(f"  ERROR: {exc}")
            failed.append((variant, str(exc)))
            continue
        for channel, report in result.reports.items():
            say(f"  {channel:<7} {report}")
        for warning in result.warnings:
            say(f"  AVISO: {warning}")
        for line in pipeline.density_lines(result.song, prepared.duration):
            say(line)
        say(f"  -> {result.folder.name}")
        built.append(result)

    if prepared.art is not None and prepared.art.name == "_cover.jpg":
        prepared.art.unlink(missing_ok=True)

    say()
    say(BAR)
    if built:
        say(f"{len(built)} carpeta(s) en {out_root}")
        for result in built:
            say(f"  {result.folder.name}")
        say()
        say("Cada carpeta ya esta completa: copiala tal cual a la carpeta de")
        say("canciones de Clone Hero y escanea.")
        say("Para editarla, abre su notes.chart en Moonscraper.")
    if failed:
        say()
        say(f"{len(failed)} variante(s) no se generaron:")
        for variant, reason in failed:
            say(f"  {variant.describe()}: {reason}")
    return 0 if built else 1
