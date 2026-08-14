# SPDX-License-Identifier: MIT
# Copyright 2026, Lukáš Czerner <lukas@czerner.cz>
"""Fixtures for the charck regression suite.

Every test drives charck.py as a subprocess, the way a user does, and
asserts on the bytes it leaves behind rather than on the report text alone.

The suite grew as a standalone script with lettered sections, one per
failure mode a review turned up. Each module says which sections it holds,
so a case can still be traced back to the bug that motivated it.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOL = str(REPO / "charck.py")
MAKEFILE = REPO / "Makefile"

# One reportable character. Most of the walking tests only need a file with
# something to say: silence from a file that had nothing proves nothing.
DASH = "a — b\n"

# The permission cases make a directory read-only and expect the write to
# fail. Root ignores the bits, so there they would assert nothing.
skip_if_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="running as root: permission bits do not apply")

# A checkout without make, or without the Makefile, is still a working
# checkout: charck itself is one stdlib module.
skip_if_no_make = pytest.mark.skipif(
    shutil.which("make") is None or not MAKEFILE.exists(),
    reason="no make, or no Makefile")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Keep the developer's own ledgers out of every run.

    charck falls back to ~/.config when XDG_CONFIG_HOME is unset, so without
    this a real ~/.config/charck/charck.toml would have a say in every test
    that does not pass --config.

    A sibling of tmp_path rather than a directory inside it, so a test that
    walks its own tmp_path never has to reckon with a home in the middle of
    the tree it is scanning.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home


@pytest.fixture
def global_ledger(isolated_home):
    """The global ledger's path, in the isolated config home.

    The directory is made but the file is not: a test that wants a global
    layer writes it, and one that does not gets a run with no global layer.
    """
    path = isolated_home / ".config" / "charck" / "charck.toml"
    path.parent.mkdir(parents=True)
    return path


@pytest.fixture
def charck(tmp_path):
    """Run charck.py, and never let a hang take the suite with it.

    The default working directory is the test's own, never the checkout:
    from there charck would walk up and find the repository's own
    .charck.toml, and a run with no --config would write into it.
    """
    def run(*args, cwd=None, timeout=60):
        return subprocess.run(
            [sys.executable, TOOL, *[str(a) for a in args]],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(tmp_path if cwd is None else cwd),
            stdin=subprocess.DEVNULL, timeout=timeout)
    return run


@pytest.fixture
def charck_background(tmp_path):
    """Launch charck without waiting for it, for the concurrency case.

    Anything still running when the test ends is killed, so a lock held by a
    stray process cannot reach the next test.
    """
    procs = []

    def launch(*args, cwd=None):
        proc = subprocess.Popen(
            [sys.executable, TOOL, *[str(a) for a in args]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=str(tmp_path if cwd is None else cwd))
        procs.append(proc)
        return proc

    yield launch
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
            # kill() only signals; wait for the lock to actually be gone.
            proc.wait(timeout=5)


@pytest.fixture
def cfg(tmp_path):
    """Write a ledger in the test's own directory."""
    def make(name, body=""):
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path
    return make


@pytest.fixture
def src(tmp_path):
    """Write a file to scan, from text or from exact bytes."""
    def make(name, data=DASH):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
        return path
    return make


@pytest.fixture
def tree(tmp_path):
    """Build a tree of files that all have something to report.

    A name ending in a slash is an empty directory, everything else a file.
    """
    def make(root, names=(), body=DASH):
        base = tmp_path / root
        base.mkdir(parents=True, exist_ok=True)
        for name in names:
            path = base / name.rstrip("/")
            if name.endswith("/"):
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return base
    return make


@pytest.fixture
def restore_mode():
    """chmod, and put the mode back however the test ends.

    A test that left a directory unwritable would take the tmp_path cleanup
    down with it, and the failure would land on whatever ran next.
    """
    saved = []

    def chmod(path, mode):
        saved.append((path, os.stat(path).st_mode))
        os.chmod(path, mode)
        return path

    yield chmod
    for path, mode in reversed(saved):
        try:
            os.chmod(path, mode)
        except OSError:
            pass


@pytest.fixture
def outside_any_repo(tmp_path):
    """Skip when TMPDIR is itself inside a work tree.

    That repository's .gitignore would then decide part of the result, which
    is the very thing the test using this is about.
    """
    for parent in tmp_path.parents:
        if (parent / ".git").exists():
            pytest.skip("TMPDIR is itself inside a work tree")
