"""The ledger: what it protects, what it refuses, how it grows.

Sections E, F, J, K, L and Q of the original suite: the live ledger and the
tool's own file are never rewritten, an appended ledger stays valid TOML,
operational errors exit 2 rather than 1, a failed write is reported, and
concurrent appends do not brick the file.
"""
import pytest

from conftest import skip_if_root


@pytest.fixture
def ledger_in_the_tree(charck, src, tmp_path):
    """A ledger sitting in the very tree it is used to fix.

    The em dash in the comment is what gives this teeth: a ledger of plain
    ASCII has nothing to report, so an unprotected run would leave it alone
    anyway and the check would pass on a tool that had lost the guard.
    """
    d = tmp_path / "tree"
    d.mkdir()
    c = d / "my.toml"
    c.write_text('# an em — dash becomes a hyphen\n'
                 '[chars."U+2014"]\naction="replace"\nto="-"\n',
                 encoding="utf-8")
    src("tree/doc.md", "a — b\n")
    before = c.read_bytes()
    charck("--config", c, "--fix", "-q", d)
    return c, before


def test_config_in_the_scanned_tree_is_not_rewritten(ledger_in_the_tree):
    c, before = ledger_in_the_tree
    assert c.read_bytes() == before, repr(c.read_bytes())


def test_config_still_parses_afterwards(charck, ledger_in_the_tree):
    c, _ = ledger_in_the_tree
    assert charck("--config", c, "--list").returncode == 0


def test_append_to_newline_less_config_stays_valid_toml(charck, cfg, src):
    c = cfg("nonl.toml", '[chars."U+2014"]\naction="ignore"')
    f = src("nonl.md", "x … y\n")
    charck("--config", c, f)
    r = charck("--config", c, f)
    assert r.returncode != 2, r.stderr[-160:]


@pytest.fixture
def self_tree(charck, cfg, tmp_path):
    """A tree holding a file named like the tool itself."""
    d = tmp_path / "selfd"
    d.mkdir()
    (d / "charck.py").write_text("grep '—' x\n", encoding="utf-8")
    c = cfg("self.toml", '[chars."U+2014"]\naction="replace"\nto="-"\n')
    return d, c, charck("--config", c, "--fix", "-q", d)


def test_self_skip_announced_even_under_q(self_tree):
    _, _, r = self_tree
    assert "self-referencing" in r.stdout, r.stdout[-200:]


def test_self_skip_makes_fix_exit_1_not_0(self_tree):
    _, _, r = self_tree
    assert r.returncode == 1, "exit=%d" % r.returncode


def test_self_file_untouched(self_tree):
    d, _, _ = self_tree
    got = (d / "charck.py").read_text(encoding="utf-8")
    assert got == "grep '—' x\n", repr(got)


def test_naming_it_directly_rewrites_it(charck, self_tree):
    # The directory and the file on one command line, so the direct name has
    # to win whichever order the walk reaches them in.
    d, c, _ = self_tree
    charck("--config", c, "--fix", "-q", d, d / "charck.py")
    got = (d / "charck.py").read_text(encoding="utf-8")
    assert got == "grep '-' x\n", repr(got)


@pytest.mark.parametrize("body", [
    '[chars."U+FFFFFF"]\naction="delete"\n',
    "chars = 5\n",
    '[chars."U+2014"]\naction="delete"\n[chars."U+02014"]\naction="delete"\n',
], ids=["out-of-range key", "chars not a table", "duplicate character"])
def test_bad_config_exits_2(charck, cfg, src, body):
    r = charck("--config", cfg("bad.toml", body), src("z.md", "a\n"))
    assert r.returncode == 2, "exit=%d %s" % (r.returncode, r.stderr[-100:])


# A missing parent is created, not an error: the global ledger lives under
# ~/.config/charck/, which will not exist on a first run.
@pytest.fixture
def missing_parent(charck, src, tmp_path):
    f = src("hasfind.md", "a — b\n")
    missing = tmp_path / "nope" / "deeper" / "x.toml"
    return missing, charck("--config", missing, f)


def test_config_in_a_missing_directory_is_created(missing_parent):
    missing, r = missing_parent
    assert missing.is_file(), "exit=%d %s" % (r.returncode, r.stderr[-120:])


def test_and_the_run_proceeds_normally(missing_parent):
    _, r = missing_parent
    assert r.returncode == 1, "exit=%d" % r.returncode


# But a parent that cannot be created is still an operational error.
@skip_if_root
def test_uncreatable_config_parent_exits_2(charck, src, restore_mode,
                                           tmp_path):
    f = src("hasfind.md", "a — b\n")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    restore_mode(blocked, 0o500)
    r = charck("--config", blocked / "sub" / "x.toml", f)
    assert r.returncode == 2, "exit=%d %s" % (r.returncode, r.stderr[-120:])


def test_config_that_is_a_directory_exits_2(charck, src, tmp_path):
    r = charck("--config", tmp_path, src("z.md", "a\n"))
    assert r.returncode == 2, "exit=%d" % r.returncode


@pytest.fixture
def unwritable_tree(charck, cfg, tree, restore_mode):
    d = tree("ro", ["a.md", "b.md"], body="a — b\n")
    c = cfg("ro.toml", '[chars."U+2014"]\naction="replace"\nto="-"\n')
    restore_mode(d, 0o500)
    return charck("--config", c, "--fix", "-q", d)


@skip_if_root
def test_unwritable_dir_exits_2_not_1(unwritable_tree):
    assert unwritable_tree.returncode == 2, \
        "exit=%d" % unwritable_tree.returncode


@skip_if_root
def test_write_failure_is_reported(unwritable_tree):
    assert "could not write" in unwritable_tree.stderr, \
        unwritable_tree.stderr[-160:]


@pytest.fixture
def after_concurrent_appends(charck_background, cfg, tree):
    """Four runs appending the same three characters at once."""
    d = tree("conc", ["f%d.md" % i for i in range(6)],
             body="a — b … c · d\n")
    c = cfg("conc.toml")
    procs = [charck_background("--config", c, "-q", d) for _ in range(4)]
    for proc in procs:
        proc.wait(timeout=60)
    return c


def test_config_still_parses_after_4_concurrent_runs(
        charck, after_concurrent_appends):
    r = charck("--config", after_concurrent_appends, "--list")
    assert r.returncode == 0, r.stderr[-200:]


def test_no_duplicate_tables(after_concurrent_appends):
    body = after_concurrent_appends.read_text(encoding="utf-8")
    assert body.count('[chars."U+2014"]') == 1, \
        "count=%d" % body.count('[chars."U+2014"]')


def test_no_duplicate_header(after_concurrent_appends):
    body = after_concurrent_appends.read_text(encoding="utf-8")
    assert body.count("decision ledger") == 1
