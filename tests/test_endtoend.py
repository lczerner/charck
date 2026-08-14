# SPDX-License-Identifier: MIT
# Copyright 2026, Lukáš Czerner <lukas@czerner.cz>
"""The whole loop, on one file that has one of everything.

Section P of the original suite: a bootstrap run writes down every character
that needs a decision and nothing that does not, the decisions then apply,
and running the same command again changes nothing.

The invisible characters are escapes, not literals. A test file for this
tool of all things does not get to carry a zero-width space nobody can see.
"""
import re

import pytest

FIXTURE = ("\ufeff---\ntitle: Luk\u00e1\u0161 Czerner\n---\n"
           "Roughly 5\u00a0km away.\nA zero\u200bwidth word.\n"
           "soft\u00adhyphen \x1b esc.\n"
           "sep\u2028here.\npua \ue000 and \ufffd moji.\n"
           "fill \u3164 braille \u2800 .\n"
           "heart \u2764\ufe0f ideo \u4e00\U000e0101.\n"
           "fam \U0001f468\u200d\U0001f469 here.\n"
           "lig \ufb01le \u00b7 dot.\n"
           "arrow \u2192 quote \u2019s dash\u2014here.\n"
           "wave \U0001f44b done.\n\ttabbed stays.\n")

DECISIONS = [("U+00A0", "replace", " "), ("U+2028", "replace", "\\n"),
             ("U+200B", "delete", ""), ("U+00AD", "delete", ""),
             ("U+001B", "delete", ""), ("U+FEFF", "delete", ""),
             ("U+E000", "delete", ""), ("U+FFFD", "delete", ""),
             ("U+3164", "delete", ""), ("U+2800", "delete", ""),
             ("U+FE0F", "delete", ""), ("U+E0101", "delete", ""),
             ("U+200D", "delete", ""), ("U+00B7", "replace", "-"),
             ("U+2014", "replace", " - "), ("U+1F44B", "ignore", "")]

INVISIBLES = ("\u200b\u00ad\x1b\ufeff\ue000\ufffd\u3164\u2800\ufe0f"
              "\U000e0101\u200d")


@pytest.fixture
def bootstrapped(charck, cfg, src):
    """The first run over the fixture, with an empty ledger."""
    c = cfg("full.toml")
    f = src("full.md", FIXTURE)
    return c, f, charck("--config", c, "-q", f)


def test_bootstrap_exits_1(bootstrapped):
    _, _, r = bootstrapped
    assert r.returncode == 1


def test_accented_latin_never_appended(bootstrapped):
    c, _, _ = bootstrapped
    text = c.read_text(encoding="utf-8")
    assert "U+00E1" not in text and "U+0161" not in text


def test_tab_and_ascii_never_appended(bootstrapped):
    c, _, _ = bootstrapped
    assert "U+0009" not in c.read_text(encoding="utf-8")


def test_invisibles_appended(bootstrapped):
    c, _, _ = bootstrapped
    text = c.read_text(encoding="utf-8")
    assert all(k in text for k in ("U+200B", "U+FEFF", "U+2028", "U+FE0F",
                                   "U+E0101", "U+3164", "U+2800", "U+FFFD"))


@pytest.fixture
def fixed(charck, bootstrapped):
    """Every character decided by hand, the way a user would, then --fix."""
    c, f, _ = bootstrapped
    text = c.read_text(encoding="utf-8")
    for key, action, to in DECISIONS:
        pattern = r'(\[chars\."%s"\]\n(?:[^\[]*))' % re.escape(key)
        block = re.search(pattern, text).group(1)
        new = block.replace('action = ""', 'action = "%s"' % action)
        new = new.replace('to     = ""', 'to     = "%s"' % to)
        text = text.replace(block, new)
    c.write_text(text, encoding="utf-8")
    charck("--config", c, "--fix", "-q", f)
    return c, f


@pytest.fixture
def fixed_text(fixed):
    _, f = fixed
    return f.read_text(encoding="utf-8")


def test_nbsp_becomes_a_space_words_not_glued(fixed_text):
    assert "Roughly 5 km away." in fixed_text


def test_zwsp_deleted(fixed_text):
    assert "A zerowidth word." in fixed_text


def test_line_separator_becomes_a_newline(fixed_text):
    assert "sep\nhere." in fixed_text


def test_invisibles_gone(fixed_text):
    assert not any(ch in fixed_text for ch in INVISIBLES)


def test_middle_dot_becomes_a_hyphen(fixed_text):
    assert " - dot." in fixed_text


def test_em_dash_collapse(fixed_text):
    assert "dash - here." in fixed_text


def test_ignored_emoji_kept(fixed_text):
    assert "\U0001f44b" in fixed_text


def test_undecided_kept(fixed_text):
    assert all(ch in fixed_text for ch in "\u2192\u2019\ufb01")


def test_accented_name_byte_identical(fixed_text):
    assert "Luk\u00e1\u0161 Czerner" in fixed_text


def test_tab_preserved(fixed_text):
    assert "\ttabbed stays." in fixed_text


@pytest.fixture
def fixed_twice(charck, fixed):
    c, f = fixed
    before, cbefore = f.read_bytes(), c.read_bytes()
    charck("--config", c, "--fix", "-q", f)
    return (c, cbefore), (f, before)


def test_idempotent_file_unchanged_on_2nd_fix(fixed_twice):
    _, (f, before) = fixed_twice
    assert f.read_bytes() == before


def test_idempotent_config_not_re_appended(fixed_twice):
    (c, cbefore), _ = fixed_twice
    assert c.read_bytes() == cbefore
