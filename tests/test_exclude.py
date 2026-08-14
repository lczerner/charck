"""--exclude: what a first run leaves out, and what it writes down.

Section Z of the original suite. The flag exists for the run before there is
a ledger to record anything in, so these tests use ledger discovery rather
than --config: an isolated config home, so no global ledger is in play, and
a tree with none above it. --no-gitignore throughout, as a .gitignore above
TMPDIR would otherwise decide part of the outcome.
"""
import os

import pytest

from conftest import skip_if_root

Z_FILES = ["x.md", "build/b.md", "sub/s.md", "sub/deep/d.md", "keep/k.md",
           "vendor/v.md", ".github/wf.md"]


@pytest.fixture
def zrun(charck):
    def run(cwd, *args):
        return charck("--no-gitignore", *args, cwd=cwd)
    return run


@pytest.fixture
def excl(tree):
    return tree("excl", Z_FILES)


@pytest.fixture
def ledger(excl):
    """Where a run in that tree records what it was told to exclude."""
    return excl / ".charck.toml"


@pytest.fixture
def other(tree):
    """A tree the ledger in `excl` is not above."""
    return tree("other")


@pytest.fixture
def empty_array(cfg):
    """A ledger with an ignore array in it, outside the walked tree."""
    return cfg("noapp.toml", '[files]\nignore = []\n')


@pytest.fixture
def first(zrun, excl):
    return zrun(excl, "--exclude", "build/", ".")


def test_a_first_run_excludes_with_no_ledger_to_say_so(first):
    assert "b.md" not in first.stdout and "x.md" in first.stdout, \
        first.stdout[:400]


def test_and_the_pattern_lands_in_the_ledger_that_run_created(first, ledger):
    led = ledger.read_text(encoding="utf-8")
    assert "[files]" in led and '"build/"' in led, led[-200:]


def test_and_recording_it_is_announced_not_silent(first):
    assert "recorded 1 ignore pattern" in first.stdout, first.stdout[-300:]


def test_the_next_run_needs_no_flag(zrun, excl, first):
    r = zrun(excl, ".")
    assert "b.md" not in r.stdout and "x.md" in r.stdout, r.stdout[:400]


@pytest.fixture
def second(zrun, excl, ledger, first):
    before = ledger.read_bytes()
    return before, zrun(excl, "--exclude", "keep/", ".")


def test_a_second_pattern_goes_into_the_array_already_there(second, ledger):
    after = ledger.read_text(encoding="utf-8")
    assert after.count("\nignore = [") == 1 and '"keep/"' in after, \
        after[-200:]


def test_and_not_a_byte_of_the_rest_of_the_ledger_moves(second, ledger):
    # The one place a ledger is edited rather than grown, so this asserts on
    # the bytes: everything but the inserted line has to be what it was.
    before, _ = second
    after = ledger.read_text(encoding="utf-8")
    assert after.replace('  "keep/",\n', "", 1).encode("utf-8") == before, \
        repr(after[-200:])


def test_and_it_takes_effect_on_the_run_that_recorded_it(second):
    _, r = second
    assert "k.md" not in r.stdout, r.stdout[:400]


def test_a_pattern_already_in_the_ledger_is_not_recorded_twice(zrun, excl,
                                                               ledger,
                                                               second):
    r = zrun(excl, "--exclude", "keep/", ".")
    assert (ledger.read_text(encoding="utf-8").count('"keep/"') == 1
            and "recorded" not in r.stdout), r.stdout[-200:]


# Anchoring is relative to the file a pattern lives in, and the ledger is
# not always the directory you are standing in. Both meanings have to
# survive.
@pytest.fixture
def anchored(zrun, excl, second):
    # Chained off `second`, so the insert lands after the last of two entries
    # and not merely after the first. An array_end that stops at the first
    # comma passes with a one-entry ledger.
    return zrun(excl / "sub", "--exclude", "/deep/", ".")


def test_an_anchored_exclude_excludes_relative_to_the_working_directory(
        anchored):
    assert "d.md" not in anchored.stdout and "s.md" in anchored.stdout, \
        anchored.stdout[:400]


def test_and_is_recorded_relative_to_the_ledger_instead(anchored, ledger):
    led = ledger.read_text(encoding="utf-8")
    assert '"/sub/deep/"' in led and '"/deep/"' not in led, led[-200:]


def test_visibly_not_behind_your_back(anchored):
    assert 'recorded as "/sub/deep/"' in anchored.stdout, \
        anchored.stdout[-300:]


def test_the_recorded_form_means_the_same_place_from_the_ledgers_own_dir(
        zrun, excl, anchored):
    r = zrun(excl, ".")
    assert "d.md" not in r.stdout and "s.md" in r.stdout, r.stdout[:400]


# ---- shapes a hand-edited array comes in ----

def test_a_comment_stays_with_the_entry_it_was_written_after(zrun, cfg,
                                                             excl):
    # A comment belongs to the entry its author wrote it after, so the new
    # entry goes below it, not between the two. The undecided U+2014 is
    # appended to the same ledger by the same run, so the assertion is on
    # the array and what surrounds it, not on the whole file.
    c = cfg("shape.toml",
            '[files]\nignore = [\n  "vendor/",   # PDF pastes\n]\n')
    zrun(excl, "--config", c, "--exclude", "keep/", "-q", ".")
    got = c.read_text(encoding="utf-8")
    assert got.startswith('[files]\nignore = [\n  "vendor/",   # PDF pastes\n'
                          '  "keep/",\n]\n'), repr(got[:120])


def test_a_single_line_array_stays_on_one_line(zrun, cfg, excl):
    c = cfg("oneline.toml", '[files]\nignore = ["vendor/"]\n')
    zrun(excl, "--config", c, "--exclude", "keep/", "-q", ".")
    got = c.read_text(encoding="utf-8")
    assert got.startswith('[files]\nignore = ["vendor/", "keep/"]\n'), \
        repr(got[:120])


def test_a_comment_on_an_entry_with_no_comma_keeps_its_entry(zrun, cfg,
                                                             excl):
    c = cfg("nocomma.toml",
            '[files]\nignore = [\n  "vendor/"   # third-party\n]\n')
    zrun(excl, "--config", c, "--exclude", "keep/", "-q", ".")
    got = c.read_text(encoding="utf-8")
    assert got.startswith('[files]\nignore = [\n  "vendor/",   # third-party'
                          '\n  "keep/",\n]\n'), repr(got[:120])


def test_an_array_written_at_column_zero_stays_at_column_zero(zrun, cfg,
                                                              excl):
    c = cfg("zeroindent.toml", '[files]\nignore = [\n"a/",\n]\n')
    zrun(excl, "--config", c, "--exclude", "keep/", "-q", ".")
    got = c.read_text(encoding="utf-8")
    assert got.startswith('[files]\nignore = [\n"a/",\n"keep/",\n]\n'), \
        repr(got[:120])


def test_a_crlf_ledger_can_record_a_pattern_too(zrun, cfg, excl):
    c = cfg("crlf.toml")
    c.write_bytes(b'[files]\r\nignore = [\r\n  "vendor/",\r\n]\r\n')
    zrun(excl, "--config", c, "--exclude", "keep/", "-q", ".")
    assert b'"keep/"' in c.read_bytes(), c.read_bytes()[:120]


# files.ignore as a dotted key is valid TOML this cannot locate for certain.
# Guessing at someone's ledger is not on, so it is left alone and said so.
@pytest.fixture
def dotted(zrun, cfg, excl):
    c = cfg("dotted.toml", 'files.ignore = ["vendor/"]\n')
    return c, zrun(excl, "--config", c, "--exclude", "keep/", "-q", ".")


def test_a_ledger_whose_ignore_array_cannot_be_found_is_left_alone(dotted):
    c, _ = dotted
    got = c.read_text(encoding="utf-8")
    assert got.startswith('files.ignore = ["vendor/"]\n') \
        and "keep/" not in got, repr(got[:120])


def test_and_the_pattern_to_add_by_hand_is_printed(dotted):
    _, r = dotted
    assert "could not record" in r.stdout and '"keep/"' in r.stdout, \
        r.stdout[-300:]


def test_a_pattern_that_could_not_be_recorded_is_exit_1_on_a_clean_run(
        zrun, cfg, excl):
    # Declining to write is not an operational failure, but it does leave
    # something to act on, so a build gate must not read it as a clean pass.
    c = cfg("dotted.toml", 'files.ignore = ["vendor/"]\n')
    (excl / "clean.md").write_text("plain ascii\n", encoding="utf-8")
    r = zrun(excl, "--config", c, "--exclude", "build/", "-q", "clean.md")
    assert r.returncode == 1 and "could not record" in r.stdout, \
        "exit=%d %s" % (r.returncode, r.stdout[-200:])


# ---- what --exclude does on the run itself ----

def test_no_append_applies_the_pattern_to_this_run(zrun, excl, empty_array):
    r = zrun(excl, "--config", empty_array, "--no-append", "--exclude",
             "keep/", ".")
    assert "k.md" not in r.stdout and "v.md" in r.stdout, r.stdout[:400]


def test_and_records_nothing(zrun, excl, empty_array):
    before = empty_array.read_bytes()
    zrun(excl, "--config", empty_array, "--no-append", "--exclude", "keep/",
         ".")
    assert empty_array.read_bytes() == before


def test_exclude_survives_no_ignore(zrun, excl, empty_array):
    # --no-ignore turns off what was recorded; a pattern given on the same
    # command line is what is being asked for right now.
    r = zrun(excl, "--config", empty_array, "--no-append", "--no-ignore",
             "--exclude", "keep/", ".")
    assert "k.md" not in r.stdout and "v.md" in r.stdout, r.stdout[:400]


def test_a_path_named_directly_is_scanned_though_exclude_names_it(zrun, excl,
                                                                  empty_array):
    r = zrun(excl, "--config", empty_array, "--no-append", "--exclude",
             "keep/", "keep/k.md")
    assert "k.md" in r.stdout, r.stdout[:400]


def test_a_recorded_bang_brings_a_built_in_default_back(zrun, cfg, excl):
    c = cfg("bang.toml", '[files]\nignore = ["!.github/"]\n')
    r = zrun(excl, "--config", c, "--no-append", ".")
    assert "wf.md" in r.stdout, r.stdout[:400]


def test_exclude_outranks_a_recorded_re_include(zrun, cfg, excl):
    c = cfg("bang.toml", '[files]\nignore = ["!.github/"]\n')
    r = zrun(excl, "--config", c, "--no-append", "--exclude", ".github/", ".")
    assert "wf.md" not in r.stdout, r.stdout[:400]


# Ours, so exit 2, exactly as an unusable pattern in a ledger is.
def test_an_unusable_exclude_is_exit_2_and_stops_the_run(zrun, excl,
                                                         empty_array):
    r = zrun(excl, "--config", empty_array, "--exclude", "a//b", "-q", ".")
    assert (r.returncode == 2 and "--exclude" in r.stderr
            and "empty path segment" in r.stderr), r.stderr[:200]


def test_and_with_the_ledger_untouched(zrun, excl, empty_array):
    before = empty_array.read_bytes()
    zrun(excl, "--config", empty_array, "--exclude", "a//b", "-q", ".")
    assert empty_array.read_bytes() == before


# An anchored pattern that could not be written down without changing
# meaning is refused before the walk, not applied and then quietly dropped.
def test_an_anchored_exclude_the_ledger_cannot_hold_stops_the_run(zrun, excl,
                                                                  other):
    elsewhere = other / "f.toml"
    elsewhere.write_text("", encoding="utf-8")
    r = zrun(excl, "--config", elsewhere, "--exclude", "sub/deep/", "-q", ".")
    assert r.returncode == 2 and "anchored" in r.stderr, r.stderr[:300]


def test_and_no_append_is_the_way_through(zrun, excl, other):
    elsewhere = other / "f.toml"
    elsewhere.write_text("", encoding="utf-8")
    r = zrun(excl, "--config", elsewhere, "--no-append", "--exclude",
             "sub/deep/", ".")
    assert r.returncode == 1 and "d.md" not in r.stdout, r.stdout[:400]


def test_an_unanchored_exclude_reaches_a_tree_outside_the_working_dir(
        zrun, excl, other, empty_array):
    # The rules match at any depth, unlike a ledger's, whose group only
    # applies under its own directory. A prefix here would make this
    # silently scan keep/.
    r = zrun(other, "--config", empty_array, "--no-append", "--exclude",
             "keep/", excl)
    assert "k.md" not in r.stdout and "v.md" in r.stdout, r.stdout[:400]


def test_v_names_exclude_as_the_source_of_the_rule(zrun, excl, empty_array):
    r = zrun(excl, "--config", empty_array, "--no-append", "-v", "--exclude",
             "keep/", ".")
    assert "[keep/ from --exclude]" in r.stdout, \
        [ln for ln in r.stdout.splitlines() if "ignored" in ln]


def test_list_shows_a_command_line_pattern_as_cli(zrun, excl, empty_array):
    r = zrun(excl, "--config", empty_array, "--list", "--exclude", "keep/")
    assert any(ln.startswith("cli") and "keep/" in ln
               for ln in r.stdout.splitlines()), r.stdout[-300:]


def test_a_walk_root_the_ledger_is_not_above_stops_the_run(zrun, excl, other,
                                                           first):
    # A ledger's patterns only apply under its own directory, so a pattern
    # recorded for a tree somewhere else would work on this run and do
    # nothing on the next.
    r = zrun(excl, "--exclude", "keep/", other)
    assert r.returncode == 2 and "could never apply" in r.stderr, \
        r.stderr[:200]


# ---- the two rewrites record_form performs ----
# Neither shows up in a report, and both produce a pattern that means
# something else when they are missed.

@pytest.fixture
def meta_tree(zrun, tree):
    root = tree("meta", ["a*b/x.md", "a*b/build/b.md", "aXb/build/c.md"])
    (root / ".charck.toml").write_text('[files]\nignore = []\n',
                                       encoding="utf-8")
    zrun(root / "a*b", "-q", "--exclude", "/build/", ".")
    return root


def test_a_glob_character_in_the_path_is_escaped_into_the_ledger(meta_tree):
    led = (meta_tree / ".charck.toml").read_text(encoding="utf-8")
    assert '"/a\\\\*b/build/"' in led, led[:200]


def test_so_the_recorded_pattern_still_names_the_one_directory_it_meant(
        zrun, meta_tree):
    r = zrun(meta_tree, "-v", ".")
    hits = [ln for ln in r.stdout.splitlines() if ln.startswith("  ignored")]
    assert (any("a*b/build" in ln for ln in hits)
            and not any("aXb/build" in ln for ln in hits)), hits


@pytest.fixture
def bang_tree(zrun, tree):
    root = tree("bang", ["sub/x.md", "sub/deep/d.md"])
    (root / ".charck.toml").write_text('[files]\nignore = ["deep/"]\n',
                                       encoding="utf-8")
    return root, zrun(root / "sub", "--exclude", "!/deep/", ".")


def test_a_leading_bang_survives_the_rewrite(bang_tree):
    root, _ = bang_tree
    led = (root / ".charck.toml").read_text(encoding="utf-8")
    assert '"!/sub/deep/"' in led, led[:200]


def test_and_the_re_include_works_on_the_run_that_recorded_it(bang_tree):
    _, r = bang_tree
    assert "d.md" in r.stdout, r.stdout[:400]


def test_and_again_from_the_ledgers_own_directory(zrun, bang_tree):
    root, _ = bang_tree
    r = zrun(root, ".")
    assert "d.md" in r.stdout, r.stdout[:400]


# The ledger anchors to the directory holding the file, symlink or not, so
# the rewrite has to use that directory and not the one the link points into.
@pytest.fixture
def linked_ledger(zrun, tree):
    root = tree("linked", ["proj/sub/x.md", "shared/"])
    (root / "shared" / "led.toml").write_text('[files]\nignore = []\n',
                                              encoding="utf-8")
    os.symlink("../shared/led.toml", root / "proj" / ".charck.toml")
    r = zrun(root / "proj" / "sub", "-q", "--exclude", "/build/",
             "--exclude", "/build/", ".")
    return root / "shared" / "led.toml", r


def test_a_symlinked_ledger_anchors_to_the_links_directory(linked_ledger):
    led, _ = linked_ledger
    assert '"/sub/build/"' in led.read_text(encoding="utf-8"), \
        led.read_text(encoding="utf-8")[:200]


def test_and_the_same_pattern_twice_is_reported_once(linked_ledger):
    _, r = linked_ledger
    assert r.stdout.count("recorded as") == 1, r.stdout[-300:]


# The insert is the one edit this makes to a ledger, so a write that cannot
# happen has to leave every decision in it exactly where it was.
@pytest.fixture
def unwritable_ledger(zrun, tree, restore_mode, tmp_path):
    root = tree("nowrite", ["x.md"])
    led = root / "led.toml"
    led.write_text('[files]\nignore = ["v/"]\n'
                   '[chars."U+2014"]\naction = "delete"\n', encoding="utf-8")
    before = led.read_bytes()
    restore_mode(root, 0o555)
    r = zrun(tmp_path, "--config", led, "--exclude", "build/", "-q",
             root / "x.md")
    return led, before, r


@skip_if_root
def test_a_ledger_that_cannot_be_written_is_left_exactly_as_it_was(
        unwritable_ledger):
    led, before, _ = unwritable_ledger
    assert led.read_bytes() == before, led.read_text(encoding="utf-8")[:200]


@skip_if_root
def test_and_that_is_exit_2_with_a_message_not_a_traceback(
        unwritable_ledger):
    _, _, r = unwritable_ledger
    assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr[-200:]


# This tool of all tools does not get to pretend a path segment cannot hold
# a newline. `.` in the any-depth prefix does not match one; `(?s:.)` does.
def test_a_newline_in_a_directory_name_does_not_defeat_an_unanchored_pattern(
        zrun, tree, empty_array):
    root = tree("od\nnl", ["s.md", "build/b.md"])
    r = zrun(root, "--config", empty_array, "--no-append", "--exclude",
             "build/", ".")
    assert "b.md" not in r.stdout and "s.md" in r.stdout, r.stdout[:400]
