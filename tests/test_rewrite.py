# SPDX-License-Identifier: MIT
# Copyright 2026, Lukáš Czerner <lukas@czerner.cz>
"""What --fix is allowed to change, and what it must leave alone.

Sections A, B, C, D, G and N of the original suite: `to` is data and not a
regex template, the rewrite may only touch what the report showed, the pass
is single and convergent, collapse keeps indentation, a symlink is followed
rather than replaced, and the summary counts in the right grammar.
"""
import pytest


def test_backslash_in_to_survives_collapse_verbatim(charck, cfg, src):
    c = cfg("bs.toml", '[chars."U+2014"]\naction="replace"\n'
                       'to="\\\\to"\ncollapse=true\n')
    f = src("bs.md", "alpha — beta\n")
    charck("--config", c, "--fix", "-q", f)
    assert f.read_bytes() == b"alpha\\tobeta\n", repr(f.read_bytes())


@pytest.fixture
def backslash_d(charck, cfg, src):
    """A `to` whose backslash escape means something to re.sub."""
    c = cfg("bs2.toml", '[chars."U+2014"]\naction="replace"\n'
                        'to="C:\\\\dir"\ncollapse=true\n')
    f = src("bs2.md", "x — y\n")
    return charck("--config", c, "--fix", "-q", f), f


def test_to_with_backslash_d_does_not_crash(backslash_d):
    r, _ = backslash_d
    assert "PatternError" not in r.stderr and r.returncode in (0, 1), \
        r.stderr[-120:]


def test_to_with_backslash_d_written_literally(backslash_d):
    _, f = backslash_d
    assert f.read_bytes() == b"xC:\\diry\n", repr(f.read_bytes())


CR_LEDGER = ('[chars."U+2014"]\naction="replace"\nto=" - "\n'
             '[chars."U+000D"]\naction="delete"\n')


def test_crlf_survives_a_u000d_delete_decision(charck, cfg, src):
    # scan_text skips the \r of a \r\n pair, so build_pattern matches
    # \r(?!\n): a decision can only reach what the report showed.
    c = cfg("cr.toml", CR_LEDGER)
    f = src("cr.md", b"line one\xe2\x80\x94here\r\nline two\r\nline three\r\n")
    charck("--config", c, "--fix", "-q", f)
    assert f.read_bytes().count(b"\r\n") == 3, repr(f.read_bytes())


def test_lone_cr_still_deleted_crlf_kept(charck, cfg, src):
    c = cfg("cr.toml", CR_LEDGER)
    f = src("cr2.md", b"a\rb\r\nc\r\n")
    charck("--config", c, "--fix", "-q", f)
    assert f.read_bytes() == b"ab\r\nc\r\n", repr(f.read_bytes())


def test_exempt_char_in_config_is_a_fatal_error(charck, cfg, src):
    # Same reason: a tab is never reported, so no decision about one could
    # ever agree with the report.
    c = cfg("tab.toml", '[chars."U+0009"]\naction="delete"\n')
    r = charck("--config", c, src("t.md", "a\tb\n"))
    assert r.returncode == 2, r.stdout + r.stderr


def test_collapse_output_is_not_re_processed(charck, cfg, src):
    c = cfg("chain.toml",
            '[chars."U+2014"]\naction="replace"\nto="\u2026"\n'
            'collapse=true\n'
            '[chars."U+2026"]\naction="replace"\nto="..."\n')
    f = src("chain.md", "a — b and \u2026 end\n")
    charck("--config", c, "--fix", "-q", f)
    got = f.read_text(encoding="utf-8")
    assert got == "a\u2026b and ... end\n", repr(got)


@pytest.fixture
def self_containing(charck, cfg, src):
    """A `to` holding the character it replaces: no fixed point exists."""
    c = cfg("conv.toml", '[chars."U+2014"]\naction="replace"\nto="\u2014-"\n')
    f = src("conv.md", "a — b\n")
    return charck("--config", c, "--fix", "-q", f), f


def test_self_containing_to_rejected_at_load(self_containing):
    r, _ = self_containing
    assert r.returncode == 2, r.stderr[-160:]


def test_self_containing_to_leaves_the_file_untouched(self_containing):
    _, f = self_containing
    assert f.read_text(encoding="utf-8") == "a — b\n"


def test_result_independent_of_config_entry_order(charck, cfg, src):
    body = ('[chars."U+2014"]\naction="replace"\nto=" \u00b7 "\n'
            'collapse=true\n',
            '[chars."U+00B7"]\naction="replace"\nto="X"\n')
    outs = []
    for i, order in enumerate([body, body[::-1]]):
        c = cfg("ord%d.toml" % i, "".join(order))
        f = src("ord%d.md" % i, "a — b\n")
        charck("--config", c, "--fix", "-q", f)
        outs.append(f.read_text(encoding="utf-8"))
    assert outs[0] == outs[1], repr(outs)


def test_collapse_preserves_leading_indentation(charck, cfg, src):
    # Absorbing the indent of a line would change YAML and Markdown nesting.
    c = cfg("ind.toml", '[chars."U+2014"]\naction="replace"\nto=" - "\n'
                        'collapse=true\n')
    f = src("ind.md", "    — nested\ntop — mid\n")
    charck("--config", c, "--fix", "-q", f)
    got = f.read_text(encoding="utf-8")
    assert got == "    - nested\ntop - mid\n", repr(got)


@pytest.fixture
def through_symlink(charck, cfg, src, tmp_path):
    real = src("real.md", "em — dash\n")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    c = cfg("sym.toml", '[chars."U+2014"]\naction="replace"\nto="-"\n'
                        'collapse=true\n')
    charck("--config", c, "--fix", "-q", link)
    return real, link


def test_symlink_is_still_a_symlink(through_symlink):
    _, link = through_symlink
    assert link.is_symlink()


def test_target_file_was_the_one_rewritten(through_symlink):
    real, _ = through_symlink
    got = real.read_text(encoding="utf-8")
    assert got == "em-dash\n", repr(got)


def test_singular_character_fixed_in_one_file(charck, cfg, src):
    c = cfg("p.toml", '[chars."U+2014"]\naction="replace"\nto="-"\n')
    f = src("p.md", "a — b\n")
    out = charck("--config", c, "--fix", "-q", f).stdout
    assert "1 character fixed in 1 file" in out, out[-200:]
