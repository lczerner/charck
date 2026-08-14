"""The Makefile lists what it has, and installs nothing system-wide.

Section T of the original suite. The only recipe really run here is `help`,
which is echo and awk. The rest are dry runs, which is safe for the recipes
this Makefile has: `make -n` does still execute a line prefixed with `+`,
and there is none. Nothing here installs, uninstalls or deletes anything,
and none of it invokes `make test`, which would recurse straight back into
this suite.
"""
import os
import subprocess

import pytest

from conftest import MAKEFILE, REPO, skip_if_no_make

pytestmark = skip_if_no_make

MKBODY = MAKEFILE.read_text(encoding="utf-8") if MAKEFILE.exists() else ""

VENV_PIP = (".venv/bin/pip install", ".venv/bin/python -m pip install")
ESCAPES = ("--target", "--prefix", "--root")


def mk(*args):
    # Drop the parent make's flags: run under `make test -j2` the sub-make
    # would otherwise warn about the unavailable jobserver on stderr.
    env = dict(os.environ)
    for key in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS"):
        env.pop(key, None)
    # DEVNULL and a timeout, because a make that leaves MAKEFILE_LIST unset
    # hands awk no file to read and it would then sit on our stdin forever.
    return subprocess.run(["make", *args], cwd=str(REPO), env=env,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=120)


def target_of(line):
    head = line.split(":", 1)[0]
    if not line[:1].islower() or ":" not in line or head != head.strip():
        return None
    return head if "=" not in head and " " not in head else None


# Read the rules themselves rather than trusting .PHONY. Deriving the list
# from .PHONY alone would leave a target that was never added to it checked
# by nothing at all, which is the mistake most likely to happen here.
TARGETS = [t for t in map(target_of, MKBODY.splitlines()) if t]
PHONY = [t for line in MKBODY.splitlines() if line.startswith(".PHONY:")
         for t in line.split(":", 1)[1].split()]


def test_every_rule_is_declared_phony():
    assert TARGETS and set(TARGETS) == set(PHONY), \
        "rules=%s phony=%s" % (sorted(TARGETS), sorted(PHONY))


@pytest.mark.parametrize("target", TARGETS)
def test_every_target_expands_without_error(target):
    assert mk("-n", target).returncode == 0


def test_help_itself_runs():
    # `-n` proves a recipe expands, never that it runs. help is the default
    # goal and the one target worth proving actually works.
    r = mk("help")
    assert r.returncode == 0, r.stderr[-200:]


def test_help_lists_every_target():
    # The failure this pins down: adding a target and forgetting its `##`
    # comment leaves it working but invisible in `make help`.
    out = mk("help").stdout
    missing = [t for t in TARGETS if ("  %s " % t) not in out]
    assert not missing, missing


def test_bare_make_prints_the_help():
    bare = mk()
    assert bare.returncode == 0 and bare.stdout == mk("help").stdout, \
        bare.stderr[-200:]


def system_wide(line):
    if "sudo" in line:
        return True
    if "pip install" not in line:
        return False
    # Anchored to the start of the command, and one pip per line: a
    # whitelist matching anywhere would exempt whatever else the line runs,
    # which a review got past with a trailing `# prefer .venv/bin/pip
    # install here` comment. --target and friends reach out of the
    # virtualenv even from the right pip.
    command = line.lstrip("\t").lstrip("@-+ ")
    return (not command.startswith(VENV_PIP)
            or line.count("pip install") != 1
            or any(opt in line for opt in ESCAPES))


def test_no_recipe_escalates_or_installs_system_wide():
    # The point of installing through pipx is that it stays under $HOME.
    # Recipe lines only: the header comment says "No sudo", and matching
    # prose here would fail on the very sentence that promises the property.
    # A pip named by its .venv/bin/ path writes into the virtualenv the venv
    # target just built. A bare `pip install` lands wherever PATH points,
    # and that is what this pins down.
    recipes = [ln for ln in MKBODY.splitlines() if ln.startswith("\t")]
    loose = [ln for ln in recipes if system_wide(ln)]
    assert not loose, loose
