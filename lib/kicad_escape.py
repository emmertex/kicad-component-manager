"""Escape strings for KiCad S-expression quoted values."""

# Regex fragment for the body of a KiCad ``"..."`` string (handles \\ and \").
KICAD_QUOTED_VALUE_RE = r"(?:[^\"\\]|\\.)*"


def escape_kicad_string(value) -> str:
    """Escape *value* for embedding inside KiCad ``"..."`` string literals."""
    if value is None:
        return ""
    s = str(value)
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("\t", "\\t")
    )


def unescape_kicad_string(value: str) -> str:
    """Decode a KiCad ``"..."`` string body (inverse of :func:`escape_kicad_string`)."""
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            esc = value[i + 1]
            if esc == "n":
                out.append("\n")
            elif esc == "t":
                out.append("\t")
            elif esc in ('"', "\\"):
                out.append(esc)
            else:
                out.append(esc)
            i += 2
            continue
        out.append(value[i])
        i += 1
    return "".join(out)
