"""Validate KiCad S-expression library files (symbols and footprints)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


class KicadFormatError(Exception):
    """Raised when KiCad file syntax is invalid."""


def tokenize_sexpr(content: str) -> list:
    """Tokenize a KiCad S-expression into atoms (parens, strings, bare atoms)."""
    tokens: list = []
    i = 0
    n = len(content)
    while i < n:
        c = content[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == ";":
            while i < n and content[i] != "\n":
                i += 1
            continue
        if c == "(":
            tokens.append("(")
            i += 1
            continue
        if c == ")":
            tokens.append(")")
            i += 1
            continue
        if c == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                ch = content[i]
                if ch == "\\":
                    if i + 1 >= n:
                        raise KicadFormatError("unterminated escape at end of file")
                    esc = content[i + 1]
                    if esc == "n":
                        buf.append("\n")
                    elif esc == "t":
                        buf.append("\t")
                    elif esc in ('"', "\\"):
                        buf.append(esc)
                    else:
                        buf.append(esc)
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    tokens.append("".join(buf))
                    break
                buf.append(ch)
                i += 1
            else:
                raise KicadFormatError("unterminated string literal")
            continue
        # bare atom (numbers, identifiers, flags like yes/no)
        start = i
        while i < n and content[i] not in " \t\r\n()\";":
            i += 1
        if start == i:
            raise KicadFormatError(f"unexpected character {c!r} at offset {i}")
        tokens.append(content[start:i])
    return tokens


def _parse_tokens(tokens: list, pos: int = 0) -> tuple:
    if pos >= len(tokens):
        raise KicadFormatError("unexpected end of input")
    if tokens[pos] != "(":
        raise KicadFormatError(f"expected '(', got {tokens[pos]!r}")
    pos += 1
    items: list = []
    while pos < len(tokens) and tokens[pos] != ")":
        tok = tokens[pos]
        if tok == "(":
            subtree, pos = _parse_tokens(tokens, pos)
            items.append(subtree)
        else:
            items.append(tok)
            pos += 1
    if pos >= len(tokens) or tokens[pos] != ")":
        raise KicadFormatError("unclosed '('")
    return items, pos + 1


def parse_sexpr(content: str) -> list:
    """Parse content into nested lists; raises KicadFormatError on bad syntax."""
    tokens = tokenize_sexpr(content)
    if not tokens:
        raise KicadFormatError("empty file")
    tree, pos = _parse_tokens(tokens, 0)
    if pos != len(tokens):
        raise KicadFormatError("trailing tokens after root expression")
    return tree


def validate_balanced_parens(content: str) -> None:
    """Fast check: parentheses balance outside quoted strings."""
    depth = 0
    in_str = False
    esc = False
    for i, c in enumerate(content):
        if esc:
            esc = False
            continue
        if in_str:
            if c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                raise KicadFormatError(f"unexpected ')' at offset {i}")
    if in_str:
        raise KicadFormatError("unterminated string literal")
    if depth != 0:
        raise KicadFormatError(f"unbalanced parentheses (depth={depth})")


def validate_kicad_file(path: Path) -> None:
    """Parse *path* as KiCad S-expression; raise KicadFormatError if invalid."""
    content = path.read_text(encoding="utf-8", errors="strict")
    validate_balanced_parens(content)
    parse_sexpr(content)


def validate_symbol_library(path: Path) -> None:
    """Validate a .kicad_sym file structure."""
    tree = parse_sexpr(path.read_text(encoding="utf-8"))
    if not tree or tree[0] != "kicad_symbol_lib":
        raise KicadFormatError("root must be kicad_symbol_lib")


_SYM_NAME_RE = re.compile(r'^\(symbol\s+"([^"\\]|\\.)*"', re.MULTILINE)


def validate_symbol_properties_escapable(path: Path) -> None:
    """Ensure every property value round-trips through the S-expression parser."""
    content = path.read_text(encoding="utf-8")
    tree = parse_sexpr(content)
    _walk_properties(tree)


def _walk_properties(node: list) -> None:
    if not isinstance(node, list) or not node:
        return
    if node[0] == "property" and len(node) >= 3:
        if not isinstance(node[1], str) or not isinstance(node[2], str):
            raise KicadFormatError("property name/value must be strings")
    for child in node:
        if isinstance(child, list):
            _walk_properties(child)


def _kicad_cli_env() -> dict[str, str]:
    """Environment for kicad-cli subprocesses.

    Cursor's AppImage sets ``APPDIR`` and prepends its ``lib`` dirs to
    ``LD_LIBRARY_PATH``; kicad-cli then looks for ``_eeschema.kiface`` beside
    the AppImage instead of under ``/usr/lib/kicad``. Strip those injections.
    """
    env = os.environ.copy()
    for key in (
        "APPDIR",
        "PYTHONHOME",
        "PYTHONPATH",
        "PERLLIB",
        "GSETTINGS_SCHEMA_DIR",
    ):
        env.pop(key, None)

    def _clean_path_list(val: str) -> str:
        return ":".join(
            p
            for p in val.split(":")
            if p
            and ".mount_Cursor" not in p
            and "/cursor/resources/" not in p.lower()
        )

    for key in ("PATH", "LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "XDG_DATA_DIRS"):
        if key in env:
            cleaned = _clean_path_list(env[key])
            if cleaned:
                env[key] = cleaned
            else:
                env.pop(key, None)

    path_parts = env.get("PATH", "").split(":")
    for extra in ("/usr/bin", "/usr/local/bin", "/bin"):
        if extra not in path_parts:
            path_parts.append(extra)
    env["PATH"] = ":".join(p for p in path_parts if p)
    return env


def _kicad_cli_exe() -> str | None:
    for candidate in ("/usr/bin/kicad-cli", "/bin/kicad-cli"):
        if Path(candidate).is_file():
            return candidate
    return shutil.which("kicad-cli", path="/usr/bin:/bin:/usr/local/bin")


def _run_kicad_cli_upgrade(
    subcmd: str,
    path: Path,
    out: Path,
    *,
    force: bool = False,
) -> None:
    """Run ``kicad-cli {fp|sym} upgrade``; raise KicadFormatError on failure."""
    cli = _kicad_cli_exe()
    if not cli:
        raise KicadFormatError("kicad-cli not installed")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cli, subcmd, "upgrade", str(path), "-o", str(out)]
    if force:
        cmd.append("--force")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_kicad_cli_env(),
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "upgrade failed").strip()
        raise KicadFormatError(f"kicad-cli {subcmd} upgrade failed: {msg}")


def kicad_cli_upgrade(path: Path, out_dir: Path) -> None:
    """Run ``kicad-cli sym upgrade``; raise KicadFormatError on failure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _run_kicad_cli_upgrade("sym", path, out_dir / path.name)


def kicad_cli_fp_upgrade(path: Path, out_dir: Path) -> None:
    """Run ``kicad-cli fp upgrade`` on the containing ``.pretty`` library."""
    lib_dir = path.parent
    if lib_dir.suffix != ".pretty":
        raise KicadFormatError(f"footprint path is not inside a .pretty library: {path}")
    if out_dir.exists():
        raise KicadFormatError(f"fp upgrade output directory already exists: {out_dir}")
    # fp upgrade writes a directory tree; output must not overlap the input path.
    _run_kicad_cli_upgrade("fp", lib_dir, out_dir, force=True)
