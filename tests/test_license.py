# SPDX-License-Identifier: MIT
# Copyright 2026, Lukáš Czerner <lukas@czerner.cz>
"""The licence is granted, and every file it covers says so.

Section AA of the original suite. pyproject.toml claimed MIT for a while
with no LICENSE next to it, which grants nothing. What is checked here is
that the claim, the grant and the per-file headers still say the same thing,
and that a file carries its own header rather than relying on being read
inside this tree.
"""
import re
import tomllib

import pytest

from conftest import REPO

HOLDER = "Copyright 2026, Lukáš Czerner <lukas@czerner.cz>"

# The test modules are globbed rather than listed: one added later carries
# the header or fails here, and there is no list to remember to update. A new
# source file outside tests/ does have to be added.
SOURCES = ["charck.py", "Makefile", "pyproject.toml"] + sorted(
    "tests/%s" % p.name for p in (REPO / "tests").glob("*.py"))


def head_of(name):
    """The first lines of a source file, or a skip if it is not there.

    Same stance as tests/test_makefile.py, and for a real case: the sdist
    ships this test file but no Makefile, so reading one unguarded here
    would end the run in an error rather than a skip.
    """
    path = REPO / name
    if not path.exists():
        pytest.skip("no %s" % name)
    # First lines only. A header further down is one a scanner, or a reader
    # opening the file, would not find.
    return "\n".join(path.read_text(encoding="utf-8").split("\n")[:6])


@pytest.fixture
def license_text():
    path = REPO / "LICENSE"
    if not path.exists():
        pytest.skip("no LICENSE")
    return path.read_text(encoding="utf-8")


@pytest.fixture
def meta():
    return tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))


def test_license_is_there_and_is_the_mit_text(license_text):
    assert ("MIT License" in license_text
            and "WITHOUT WARRANTY OF ANY KIND" in license_text
            and "shall be included in" in license_text), \
        repr(license_text[:80])


def test_and_it_names_the_copyright_holder(license_text):
    assert HOLDER in license_text, repr(license_text[:200])


@pytest.mark.parametrize("name", SOURCES)
def test_carries_the_spdx_line(name):
    head = head_of(name)
    assert "# SPDX-License-Identifier: MIT" in head, repr(head[:120])


@pytest.mark.parametrize("name", SOURCES)
def test_carries_the_copyright_line(name):
    head = head_of(name)
    assert "# " + HOLDER in head, repr(head[:120])


def test_charck_still_starts_with_the_shebang():
    # The header goes under the shebang, never above it: a `#!` on line 2 is
    # not a shebang, and the file stops being runnable as itself.
    got = (REPO / "charck.py").read_bytes()
    assert got.startswith(b"#!/usr/bin/env python3\n"), repr(got[:40])


def test_pyproject_declares_the_same_licence(meta):
    assert meta["project"].get("license") == "MIT", \
        meta["project"].get("license")


def test_and_ships_the_license_file_with_the_wheel(meta):
    assert meta["project"].get("license-files") == ["LICENSE"], \
        meta["project"].get("license-files")


def test_and_no_classifier_the_expression_superseded(meta):
    # PEP 639 replaced the classifier with the expression above, and
    # setuptools refuses to build a project that states both. Nothing else
    # here would notice: adding the classifier back leaves the suite green
    # and the build broken.
    bad = [c for c in meta["project"].get("classifiers", [])
           if c.startswith("License ::")]
    assert not bad, bad


def test_and_requires_a_setuptools_that_understands_both(meta):
    # An SPDX string in `license` and `license-files` are both PEP 639,
    # which setuptools grew in 77. A lower floor here builds metadata that
    # silently drops the licence. Read the number rather than the spelling:
    # `setuptools >= 77` is the same requirement, and raising the floor
    # later is not a failure.
    req = " ".join(meta["build-system"]["requires"])
    floor = re.search(r"setuptools\s*>=\s*(\d+)", req)
    assert floor is not None and int(floor.group(1)) >= 77, req


def test_the_readme_says_what_the_licence_is():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "## License" in readme and "MIT" in readme and "`LICENSE`" in readme
