"""`tui.ask_indices`: leer una lista de numeros de un prompt de consola.

Se elige por indice y no por nombre a proposito (los nombres de pista de un
transcriptor traen guiones bajos y acentos que cp1252 no siempre imprime), asi
que este parseo es la unica puerta de entrada del modo interactivo y tiene que
aguantar lo que escriba alguien con las manos.

Las dos propiedades que importan y no son obvias:

- **reintenta** en vez de abortar cuando lo escrito no vale, porque la alternativa
  es perder el trabajo de haber contestado ya la mitad de los canales;
- con stdin cerrado levanta `Aborted` en vez de colgarse, que es la diferencia
  entre un error legible y un .bat que se queda mirando al vacio.
"""

from __future__ import annotations

import builtins

import pytest

import tui


@pytest.fixture
def responder(monkeypatch):
    """Monkeypatchea `input` con una cola de respuestas.

    Devuelve la lista de prompts que se pidieron, para poder comprobar cuantas
    veces se reintento.
    """
    def factory(*answers: str) -> list[str]:
        cola = list(answers)
        prompts: list[str] = []

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            if not cola:
                raise AssertionError("ask_indices pidio mas respuestas de las "
                                     f"previstas (prompts: {prompts})")
            return cola.pop(0)

        monkeypatch.setattr(builtins, "input", fake_input)
        return prompts

    return factory


# --------------------------------------------------------------------------
# Parseo
# --------------------------------------------------------------------------

def test_it_reads_numbers_separated_by_commas(responder):
    responder("1,3")

    assert tui.ask_indices("> ", 5) == [0, 2]


def test_it_reads_numbers_separated_by_spaces(responder):
    """La coma se escribe mal y el espacio no: se aceptan los dos."""
    responder("1 3")

    assert tui.ask_indices("> ", 5) == [0, 2]


def test_it_reads_a_mix_of_commas_and_spaces(responder):
    responder(" 1, 3 4 ,5 ")

    assert tui.ask_indices("> ", 5) == [0, 2, 3, 4]


def test_a_single_number_works(responder):
    responder("2")

    assert tui.ask_indices("> ", 5) == [1]


def test_the_indexes_come_back_zero_based(responder):
    """Se pregunta en 1-based (lo que ve el usuario) y se devuelve 0-based."""
    responder("1")

    assert tui.ask_indices("> ", 1) == [0]


def test_the_boundaries_of_the_range_are_valid(responder):
    responder("1,6")

    assert tui.ask_indices("> ", 6) == [0, 5]


# --------------------------------------------------------------------------
# Orden y duplicados
# --------------------------------------------------------------------------

def test_it_keeps_the_order_the_user_typed(responder):
    """Importa: es el orden en que se suman dos pistas en un canal."""
    responder("3,1,2")

    assert tui.ask_indices("> ", 5) == [2, 0, 1]


def test_it_drops_duplicates_keeping_the_first_occurrence(responder):
    responder("3,1,3,1,2")

    assert tui.ask_indices("> ", 5) == [2, 0, 1]


def test_a_repeated_number_alone_collapses_to_one(responder):
    responder("2 2 2")

    assert tui.ask_indices("> ", 5) == [1]


# --------------------------------------------------------------------------
# Vacio
# --------------------------------------------------------------------------

def test_an_empty_answer_means_leave_it_out(responder):
    prompts = responder("")

    assert tui.ask_indices("> ", 5) == []
    assert len(prompts) == 1


def test_an_answer_of_only_spaces_is_also_empty(responder):
    responder("   ")

    assert tui.ask_indices("> ", 5) == []


def test_when_empty_is_not_allowed_it_asks_again(responder, capsys):
    prompts = responder("", "2")

    assert tui.ask_indices("> ", 5, allow_empty=False) == [1]
    assert len(prompts) == 2
    assert "al menos un numero" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Reintentos
# --------------------------------------------------------------------------

def test_out_of_range_is_rejected_and_asked_again(responder, capsys):
    prompts = responder("9", "1,3")

    assert tui.ask_indices("> ", 5) == [0, 2]
    assert len(prompts) == 2
    salida = capsys.readouterr().out
    assert "Fuera de rango: 9" in salida
    assert "Validos 1-5" in salida


def test_zero_is_out_of_range_because_the_list_is_one_based(responder, capsys):
    responder("0", "1")

    assert tui.ask_indices("> ", 5) == [0]
    assert "Fuera de rango: 0" in capsys.readouterr().out


def test_a_negative_number_is_out_of_range(responder, capsys):
    responder("-2", "1")

    assert tui.ask_indices("> ", 5) == [0]
    assert "Fuera de rango: -2" in capsys.readouterr().out


def test_every_bad_number_is_named_not_just_the_first(responder, capsys):
    responder("1,7,9", "1")

    assert tui.ask_indices("> ", 5) == [0]
    assert "Fuera de rango: 7, 9" in capsys.readouterr().out


def test_something_that_is_not_a_number_is_rejected_and_asked_again(responder,
                                                                   capsys):
    prompts = responder("bateria", "2")

    assert tui.ask_indices("> ", 5) == [1]
    assert len(prompts) == 2
    assert "Solo numeros separados por comas (1-5)" in capsys.readouterr().out


def test_a_half_numeric_answer_is_rejected_whole(responder):
    """"1,x" no se acepta a medias: o se entiende toda la linea o se repregunta."""
    responder("1,x", "1")

    assert tui.ask_indices("> ", 5) == [0]


def test_it_keeps_retrying_as_long_as_it_takes(responder):
    prompts = responder("x", "0", "99", "1,2,x", "2 4")

    assert tui.ask_indices("> ", 5) == [1, 3]
    assert len(prompts) == 5


def test_the_prompt_is_the_one_it_was_given(responder):
    prompts = responder("x", "1")

    tui.ask_indices("  guitar  > ", 5)

    assert prompts == ["  guitar  > ", "  guitar  > "]


# --------------------------------------------------------------------------
# Sin terminal
# --------------------------------------------------------------------------

def test_a_closed_stdin_aborts_instead_of_hanging(monkeypatch):
    """Es el caso del .bat lanzado sin consola: hay que salir diciendolo."""
    def fake_input(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)

    with pytest.raises(tui.Aborted) as exc:
        tui.ask_indices("> ", 5)

    assert "no hay terminal" in str(exc.value)
    assert "linea de comandos" in str(exc.value)


def test_a_closed_stdin_aborts_even_in_the_middle_of_retrying(monkeypatch):
    """Que la primera respuesta fuera mala no debe convertirlo en un bucle."""
    cola = ["x", "0"]

    def fake_input(prompt: str = "") -> str:
        if cola:
            return cola.pop(0)
        raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)

    with pytest.raises(tui.Aborted):
        tui.ask_indices("> ", 5)


def test_a_ctrl_c_is_an_abort_and_not_a_traceback(monkeypatch):
    def fake_input(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", fake_input)

    with pytest.raises(tui.Aborted) as exc:
        tui.ask_indices("> ", 5)

    assert "cancelado" in str(exc.value)


def test_aborted_is_not_swallowed_as_a_generic_error():
    """`main` la trata aparte de los errores: no es un fallo que rastrear."""
    assert issubclass(tui.Aborted, RuntimeError)


# --------------------------------------------------------------------------
# say
# --------------------------------------------------------------------------

class Consola:
    """Un stdout de mentira con el encoding que se le diga."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self.escrito: list[str] = []

    def write(self, text: str) -> int:
        self.escrito.append(text)
        return len(text)

    def flush(self) -> None:
        pass


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8", "ascii"])
def test_say_survives_characters_the_console_cannot_encode(monkeypatch, encoding):
    """Los nombres de pista traen acentos y la consola de Windows va en cp1252.

    Lo que no puede pasar es que imprimir un titulo tumbe la generacion con un
    UnicodeEncodeError: se sustituye lo que no se pueda escribir y se sigue.
    """
    consola = Consola(encoding)
    monkeypatch.setattr(tui.sys, "stdout", consola)

    tui.say("Proyecto Uno - TIBURÓN 中")

    salida = "".join(consola.escrito)
    assert "Proyecto Uno" in salida
    assert salida.endswith("\n")


def test_say_works_even_if_stdout_declares_no_encoding(monkeypatch):
    class SinEncoding:
        encoding = None
        escrito: list[str] = []

        def write(self, text): self.escrito.append(text); return len(text)
        def flush(self): pass

    consola = SinEncoding()
    monkeypatch.setattr(tui.sys, "stdout", consola)

    tui.say("hola")

    assert "hola" in "".join(consola.escrito)


def test_say_with_no_argument_prints_a_blank_line(capsys):
    tui.say()

    assert capsys.readouterr().out == "\n"
