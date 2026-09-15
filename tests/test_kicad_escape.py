import pytest

from lib.kicad_escape import escape_kicad_string
from lib.kicad_validate import (
    KicadFormatError,
    parse_sexpr,
    tokenize_sexpr,
    validate_balanced_parens,
)


@pytest.mark.parametrize(
    "raw,escaped",
    [
        ('plain', "plain"),
        ('say "hello"', 'say \\"hello\\"'),
        ("back\\slash", "back\\\\slash"),
        ("line\nbreak", "line\\nbreak"),
        ("tab\there", "tab\\there"),
        ("±10% 10kΩ", "±10% 10kΩ"),
        ('a"b\nc', 'a\\"b\\nc'),
    ],
)
def test_escape_kicad_string(raw, escaped):
    assert escape_kicad_string(raw) == escaped


def test_escape_none():
    assert escape_kicad_string(None) == ""


def test_escaped_string_round_trips_in_sexpr():
    value = escape_kicad_string('desc with "quotes" and\nnewline ±1%')
    content = f'(property "Description" "{value}")'
    validate_balanced_parens(content)
    tree = parse_sexpr(content)
    assert tree[2] == 'desc with "quotes" and\nnewline ±1%'


def test_tokenizer_rejects_unterminated_string():
    with pytest.raises(KicadFormatError):
        tokenize_sexpr('(property "broken)')
