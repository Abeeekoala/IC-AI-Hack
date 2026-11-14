#!/usr/bin/env bash
# Create a Python 3.10 virtualenv with the pinned Opentrons simulator, for VaxForge S9.
#
#   bash tools/setup-ot-venv.sh [VENV_DIR]        default VENV_DIR: <repo root>/.venv-ot
#
# Why a second environment: opentrons==7.5.0 pins pydantic<2 and numpy<2 and only imports on
# Python 3.10, while the rest of stream C needs pydantic>=2. s9_validate.find_ot_python() finds
# this venv and runs the simulator in it as a subprocess.
#
# Re-running is safe: a venv that already has the pinned opentrons is left untouched.
# The first run needs network access to PyPI (pip install). Works in Git Bash on Windows and on
# Linux/macOS (written for bash 3.2). Exit code 0 = ready, 1 = something is missing.

set -euo pipefail

case "${1:-}" in
  -h|--help)
    cat <<'USAGE'
Usage: bash tools/setup-ot-venv.sh [VENV_DIR]

Creates a Python 3.10 virtualenv at VENV_DIR (default: <repo root>/.venv-ot), installs
requirements-streamc-ot.txt (opentrons==7.5.0), checks `import opentrons; print(opentrons.__version__)`,
and prints the `export VAXFORGE_OT_PYTHON=...` line to use. Safe to re-run.
USAGE
    exit 0
    ;;
  -*)
    printf 'error: unknown option: %s (try --help)\n' "$1" >&2
    exit 1
    ;;
esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
req="$repo/requirements-streamc-ot.txt"
default_venv="$repo/.venv-ot"
venv="${1:-$default_venv}"

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Git Bash on Windows: accept C:\x\y and C:/x/y as well as /c/x/y. No-op where cygpath does not exist.
if command -v cygpath >/dev/null 2>&1; then
  venv="$(cygpath -u "$venv")"
fi

[ -f "$req" ] || die "missing $req"
want="$(sed -n 's/^opentrons==\([0-9][0-9.]*\).*$/\1/p' "$req" | head -n 1 || true)"
[ -n "$want" ] || die "no 'opentrons==X.Y.Z' line found in $req"

# True when "$@" (an interpreter command) really is Python 3.10. Checking the version, not just the name,
# skips the Microsoft Store stub on Windows and pyenv shims whose version is not installed.
is_py310() { "$@" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 10) else 1)' >/dev/null 2>&1; }

# Sets the array PY310 to a command that starts Python 3.10, or returns 1.
find_py310() {
  if command -v python3.10 >/dev/null 2>&1 && is_py310 python3.10; then
    PY310=(python3.10)
  elif command -v py >/dev/null 2>&1 && is_py310 py -3.10; then
    PY310=(py -3.10)
  else
    return 1
  fi
}

# Prints the python inside a venv: Scripts/ on Windows, bin/ elsewhere.
venv_python() {
  local d="$1" p
  for p in "$d/Scripts/python.exe" "$d/Scripts/python" "$d/bin/python"; do
    if [ -x "$p" ]; then
      printf '%s\n' "$p"
      return 0
    fi
  done
  return 1
}

# 1. Create the venv unless one is already there.
if [ -f "$venv/pyvenv.cfg" ]; then
  say "reusing existing virtualenv: $venv"
else
  if [ -e "$venv" ]; then
    if [ ! -d "$venv" ] || [ -n "$(ls -A "$venv")" ]; then
      die "$venv exists but is neither an empty directory nor a virtualenv; pass another path or remove it"
    fi
  fi
  if ! find_py310; then
    cat >&2 <<'NOPY'
error: Python 3.10 was not found (tried: python3.10, py -3.10).
Opentrons 7.5.0 only imports on Python 3.10, so it needs its own interpreter next to your main one.
Install Python 3.10, then run this script again:
  Windows (Python install manager):  py install 3.10
  uv (any OS):                       uv python install 3.10
  pyenv (Linux/macOS):               pyenv install 3.10      (then make python3.10 resolve, e.g. pyenv shell 3.10)
  Debian/Ubuntu:                     sudo apt install python3.10 python3.10-venv
  macOS with Homebrew:               brew install python@3.10
Afterwards 'python3.10 --version' (or 'py -3.10 --version' on Windows) must work in this shell.
NOPY
    exit 1
  fi
  say "creating virtualenv with ${PY310[*]}: $venv"
  "${PY310[@]}" -m venv "$venv" || die "could not create the virtualenv (Debian/Ubuntu: install the python3.10-venv package)"
fi

# 2. The venv must be Python 3.10, otherwise opentrons will not import.
vpy="$(venv_python "$venv")" || die "no python found inside $venv (is it a virtualenv?)"
mm="$("$vpy" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" || die "cannot run $vpy"
[ "$mm" = "3.10" ] || die "$venv uses Python $mm, but Opentrons $want needs Python 3.10; remove it and run this script again"

# 3. Install the pinned opentrons unless it is already there, then verify by importing it.
installed_version() { "$vpy" -c 'import opentrons; print(opentrons.__version__)' 2>/dev/null || true; }

have="$(installed_version)"
if [ "$have" = "$want" ]; then
  say "opentrons $want is already installed"
else
  if [ -n "$have" ]; then
    say "found opentrons $have, need $want"
  fi
  say "installing $(basename "$req") (needs network access to PyPI)"
  "$vpy" -m pip install --disable-pip-version-check -r "$req" || die "pip install failed; see the output above"
  have="$(installed_version)"
fi
[ "$have" = "$want" ] || die "expected opentrons $want in $venv but got '${have:-import failed}'"

# 4. Report. The interpreter path is printed with forward slashes, which both Git Bash and Windows Python accept.
if command -v cygpath >/dev/null 2>&1; then
  exe="$(cygpath -m "$vpy")"
else
  exe="$(cd "$(dirname "$vpy")" && pwd)/$(basename "$vpy")"
fi

say "Python $mm, opentrons $have  (checked with: import opentrons; print(opentrons.__version__))"
say "ready: $venv"
if [ "$venv" = "$default_venv" ]; then
  say "this is <repo root>/.venv-ot, which find_ot_python() also finds without the variable below"
fi
say "to point VaxForge at it, run this line (add it to your shell profile to keep it):"
case "$exe" in
  *\'*|*\"*|*\$*|*\`*|*\\*) printf 'export VAXFORGE_OT_PYTHON=%q\n' "$exe" ;;
  *) printf 'export VAXFORGE_OT_PYTHON="%s"\n' "$exe" ;;
esac
