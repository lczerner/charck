"""What the report says, and what it refuses to say.

Sections H, I, M, O and R of the original suite: binary detection, files
that are not files, column arithmetic and control sanitising, --ext, and the
undecided count.
"""
import os

import pytest


@pytest.fixture
def blob(src):
    """A NUL well past the first 8 KiB, which a prefix sniff would miss."""
    return src("blob.dat", b"A" * 9000 + b"\x00" + b"B" * 40)


def test_nul_past_8_kib_is_still_detected_as_binary(charck, cfg, blob):
    r = charck("--config", cfg("b.toml"), "-q", "-v", blob)
    assert "binary (NUL byte)" in r.stdout, r.stdout[-160:]


# One line per unreadable file buries the findings in a tree full of images,
# so the reason is a -v question and the count carries it by default.
def test_a_skipped_file_is_not_named_without_v(charck, cfg, blob):
    r = charck("--config", cfg("b.toml"), "-q", blob)
    assert "binary (NUL byte)" not in r.stdout, r.stdout[-160:]


def test_but_the_summary_still_counts_it(charck, cfg, blob):
    r = charck("--config", cfg("b.toml"), "-q", blob)
    assert "1 skipped" in r.stdout, r.stdout[-160:]


def test_fifo_in_a_walked_tree_does_not_hang(charck, cfg, tree):
    d = tree("fifo", ["ok.md"])
    os.mkfifo(d / "pipe")
    # A read that blocks forever would hang here; the timeout is the check.
    r = charck("--config", cfg("f.toml"), "-q", d, timeout=10)
    assert r.returncode in (0, 1), r.stderr[-160:]


def test_multiple_trailing_crs_reported_at_distinct_columns(charck, cfg, src):
    out = charck("--config", cfg("r.toml"), src("r.md", b"ab\r\r\r\n")).stdout
    assert "col   3" in out and "col   4" in out, out[:300]


def test_raw_control_chars_never_echoed_into_the_report(charck, cfg, src):
    f = src("vt.md", b"a\x0bb \xe2\x80\x94 c\n")
    out = charck("--config", cfg("r2.toml"), f).stdout
    assert "\x0b" not in out, repr(out[:200])


def test_ext_md_does_not_match_a_file_named_cmd(charck, cfg, tree):
    d = tree("extd", ["cmd", "real.md"])
    out = charck("--config", cfg("e.toml"), "--ext", "md", "-q", d).stdout
    assert "1 scanned" in out, out[-200:]


# Regressions here read as "1 character needs a decision" no matter how many
# there really are, if the set comprehension collects a stale loop variable.
def test_counts_all_four_undecided_characters(charck, cfg, src):
    f = src("count.md", "em — dash, ’ quote, · dot, → arrow\n")
    out = charck("--config", cfg("count.toml"), "-q", f).stdout
    assert "4 characters need a decision" in out, \
        [ln for ln in out.splitlines() if "decision" in ln]


def test_singular_phrasing_for_exactly_one(charck, cfg, src):
    f = src("count2.md", "just — one\n")
    out = charck("--config", cfg("count2.toml"), "-q", f).stdout
    assert "1 character needs a decision" in out, \
        [ln for ln in out.splitlines() if "decision" in ln]
