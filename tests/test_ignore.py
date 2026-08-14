# SPDX-License-Identifier: MIT
# Copyright 2026, Lukáš Czerner <lukas@czerner.cz>
"""Which files a walk reaches, and which the ledger keeps it away from.

Sections U, U2, W and X of the original suite: gitignore-syntax patterns in
our own ledger, what a glob may and may not cross, an unusable pattern being
exit 2 rather than a no-op, and the corners where gitignore syntax and a
Python regex disagree.

--no-gitignore throughout: this is about the ledger, and a .gitignore
somewhere above TMPDIR would otherwise decide part of the outcome.
"""
import subprocess

import pytest

from conftest import DASH

IG_FILES = ["keep.md", "app.js", "app.min.js", "foo", "build/x.md",
            "vendor/lib.md", "vendor/keep.md", ".github/wf.md",
            "node_modules/dep.md", "deep/a/b/c.md", "sub/one.md"]


@pytest.fixture
def ig(charck, tree):
    """A tree, and a ledger sitting in it.

    The ledger is in the tree it describes, which is what anchoring is
    relative to: a pattern with a slash means "under this file's own
    directory". The !vendor/keep.md is here to be defeated: vendor/ is
    pruned, so the walk never reaches the file the re-include names.
    """
    root = tree("ig", IG_FILES)
    ledger = root / "u.toml"
    ledger.write_text('[files]\nignore = ["build/", "*.min.js", "vendor/", '
                      '"!vendor/keep.md", "!.github/", "foo/"]\n',
                      encoding="utf-8")

    def walked(*args):
        return charck("--config", ledger, "--no-append", "--no-gitignore",
                      *args).stdout

    return root, ledger, walked


def test_an_ignored_directory_is_pruned(ig):
    root, _, walked = ig
    out = walked(root)
    assert "build/x.md" not in out, out[:400]


def test_a_file_pattern_skips_that_file_and_not_its_neighbour(ig):
    root, _, walked = ig
    out = walked(root)
    assert "app.min.js" not in out and "app.js" in out, out[:400]


def test_a_built_in_default_still_applies(ig):
    root, _, walked = ig
    out = walked(root)
    assert "node_modules/dep.md" not in out, out[:400]


def test_bang_re_includes_what_a_built_in_default_hides(ig):
    root, _, walked = ig
    out = walked(root)
    assert ".github/wf.md" in out, out[:400]


def test_a_directory_only_pattern_does_not_match_a_file_of_that_name(ig):
    root, _, walked = ig
    out = walked(root)
    assert "%s/foo\n" % root in out, out[:400]


def test_everything_unmatched_is_still_scanned(ig):
    root, _, walked = ig
    out = walked(root)
    assert ("keep.md" in out and "sub/one.md" in out
            and "deep/a/b/c.md" in out), out[:400]


def test_the_summary_counts_what_was_ignored(ig):
    # build, vendor, node_modules, app.min.js. A pruned directory counts once
    # and its contents are never enumerated, which is the point of pruning.
    root, _, walked = ig
    out = walked(root)
    assert "4 ignored" in out, \
        [ln for ln in out.splitlines() if "scanned" in ln]


def test_v_names_the_pattern_that_ignored_a_path_and_where_it_came_from(ig):
    root, ledger, walked = ig
    out = walked("-v", root)
    assert ("[vendor/ from %s]" % ledger in out
            and "[node_modules/ from built-in defaults]" in out), \
        [ln for ln in out.splitlines() if "ignored" in ln]


def test_a_re_include_cannot_reach_under_a_pruned_directory(ig):
    root, _, walked = ig
    assert "vendor/keep.md" not in walked(root)


def test_a_re_include_does_reach_under_a_directory_only_filtered(charck, ig):
    # The same re-include works when the parent is filtered rather than
    # pruned, which is what makes the check above about pruning and not `!`.
    root, _, _ = ig
    loose = root / "loose.toml"
    loose.write_text('[files]\nignore = ["vendor/*", "!vendor/keep.md"]\n',
                     encoding="utf-8")
    out = charck("--config", loose, "--no-append", "--no-gitignore",
                 root).stdout
    assert "vendor/keep.md" in out and "vendor/lib.md" not in out, out[:400]


def test_a_directory_named_directly_is_scanned_though_a_pattern_matches(ig):
    root, _, walked = ig
    assert "vendor/lib.md" in walked(root / "vendor")


def test_a_file_named_directly_is_scanned_though_a_pattern_matches(ig):
    root, _, walked = ig
    assert "app.min.js" in walked(root / "app.min.js")


def test_no_ignore_reaches_an_ignored_tree(charck, ig):
    root, ledger, _ = ig
    out = charck("--config", ledger, "--no-append", "--no-ignore",
                 root).stdout
    assert "node_modules/dep.md" in out, out[:300]


def test_no_ignore_reports_nothing_as_ignored(charck, ig):
    root, ledger, _ = ig
    out = charck("--config", ledger, "--no-append", "--no-ignore",
                 root).stdout
    assert "0 ignored" in out, \
        [ln for ln in out.splitlines() if "scanned" in ln]


@pytest.fixture
def ig_fixed(charck, ig):
    root, _, _ = ig
    ledger = root / "fix.toml"
    ledger.write_text('[files]\nignore = ["vendor/", "*.min.js"]\n'
                      '[chars."U+2014"]\naction = "replace"\nto = "-"\n',
                      encoding="utf-8")
    charck("--config", ledger, "--no-append", "--no-gitignore", "--fix",
           "-q", root)
    return root


def test_fix_leaves_an_ignored_file_byte_identical(ig_fixed):
    got = (ig_fixed / "vendor" / "lib.md").read_text(encoding="utf-8")
    assert got == DASH, repr(got)


def test_fix_does_rewrite_what_was_scanned(ig_fixed):
    got = (ig_fixed / "keep.md").read_text(encoding="utf-8")
    assert got == "a - b\n", repr(got)


# ---- glob syntax: what a * may and may not cross ----

@pytest.fixture
def globbed(charck, tree):
    root = tree("globs", ["x.md", "a/x.md", "a/y.md", "a/b/z.md"])
    ledger = root / "g.toml"

    def run(pattern):
        ledger.write_text('[files]\nignore = ["%s"]\n' % pattern,
                          encoding="utf-8")
        return root, charck("--config", ledger, "--no-append",
                            "--no-gitignore", root).stdout

    return run


def test_star_does_not_cross_a_slash(globbed):
    _, out = globbed("a/*.md")
    assert "a/y.md" not in out and "a/b/z.md" in out, out[:400]


def test_globstar_crosses_a_slash(globbed):
    _, out = globbed("a/**/z.md")
    assert "a/b/z.md" not in out and "a/y.md" in out, out[:400]


def test_a_bare_name_matches_at_any_depth(globbed):
    root, out = globbed("x.md")
    assert "a/x.md" not in out and "%s/x.md" % root not in out, out[:400]


def test_a_leading_slash_anchors_to_the_ledgers_own_directory(globbed):
    root, out = globbed("/x.md")
    assert "a/x.md" in out and "%s/x.md\n" % root not in out, out[:400]


def test_a_character_class_matches_within_one_segment(globbed):
    _, out = globbed("[xy].md")
    assert ("a/x.md" not in out and "a/y.md" not in out
            and "a/b/z.md" in out), out[:400]


def test_a_leading_globstar_is_the_any_depth_form(globbed):
    _, out = globbed("**/z.md")
    assert "a/b/z.md" not in out, out[:400]


# ---- an unusable pattern in our own ledger is exit 2, not a no-op ----

@pytest.mark.parametrize("body", [
    '[files]\nignore = [""]\n',
    '[files]\nignore = ["!"]\n',
    '[files]\nignore = ["foo\\\\"]\n',
    '[files]\nignore = ["../x"]\n',
    '[files]\nignore = ["a//b"]\n',
    '[files]\nignore = ["[z-a].md"]\n',
    '[files]\nignore = ["[[:bogus:]].md"]\n',
    '[files]\nignore = "build/"\n',
    '[files]\nignore = [5]\n',
    '[files]\nignores = ["build/"]\n',
    "files = 5\n",
], ids=["empty pattern", "lone !", "trailing backslash", ".. segment",
        "empty path segment", "uncompilable character class",
        "unknown POSIX class", "ignore not an array", "non-string element",
        "unknown key in [files]", "files not a table"])
def test_unusable_pattern_in_our_ledger_exits_2(charck, cfg, src, body):
    r = charck("--config", cfg("w.toml", body), "--no-append",
               src("w.md", "a\n"))
    assert r.returncode == 2, "exit=%d %s" % (r.returncode, r.stderr[-120:])


@pytest.fixture
def in_global(charck, global_ledger, tree):
    """Run in a tree whose only ledger is the global one."""
    where = tree("wdir", ["app.js", "app.min.js"])

    def run(body, *args):
        global_ledger.write_text(body, encoding="utf-8")
        return charck("--no-append", "--no-gitignore", *args, cwd=where)

    return run


def test_an_anchored_pattern_in_the_global_ledger_exits_2(in_global):
    # Every rule is relative to the file it was written in, and the global
    # ledger has no directory to anchor to.
    r = in_global('[files]\nignore = ["src/x.md"]\n', ".")
    assert r.returncode == 2, "exit=%d %s" % (r.returncode, r.stderr[-160:])


def test_and_the_error_says_where_the_pattern_belongs(in_global):
    r = in_global('[files]\nignore = ["src/x.md"]\n', ".")
    assert ".charck.toml" in r.stderr, r.stderr[-160:]


def test_an_unanchored_pattern_in_the_global_ledger_applies_everywhere(
        in_global):
    r = in_global('[files]\nignore = ["*.min.js"]\n', ".")
    assert "app.min.js" not in r.stdout and "app.js" in r.stdout, \
        r.stdout[:300]


# ---- the corners where gitignore and Python regex disagree ----
# Every case here was a real divergence from `git ls-files --others
# --exclude-standard`, and each one hid files from the scan rather than
# showing too many, which is the direction that makes a build gate lie.

@pytest.fixture
def cornered(charck, tree):
    # Single-letter basenames, so a class matches the name and not just part
    # of it, plus one longer name every class here must leave alone.
    root = tree("corners", ["w.md", "a.md", "long.md", "d1/b.md", "d1/w.md",
                            "d1/x/b.md"])
    ledger = root / "x.toml"

    def run(pattern):
        ledger.write_text('[files]\nignore = ["%s"]\n' % pattern,
                          encoding="utf-8")
        return charck("--config", ledger, "--no-append", "--no-gitignore",
                      root)

    return root, ledger, run


def test_a_backslash_in_a_character_class_is_a_literal(cornered):
    _, _, run = cornered
    out = run("[\\\\w].md").stdout
    assert ("a.md" in out and "d1/b.md" in out and "w.md" not in out), \
        out[:400]


def test_a_slash_inside_a_character_class_never_matches_a_separator(cornered):
    _, _, run = cornered
    out = run("d1[/]a").stdout
    assert "d1/b.md" in out and "a.md" in out, out[:400]


def test_globstar_is_a_plain_star_where_it_is_not_a_whole_segment(cornered):
    _, _, run = cornered
    out = run("d1/**b.md").stdout
    assert "d1/x/b.md" in out and "d1/b.md" not in out, out[:400]


def test_a_posix_class_is_spelled_out_not_passed_through(cornered):
    _, _, run = cornered
    r = run("[[:alpha:]].md")
    assert "w.md" not in r.stdout and "long.md" in r.stdout, r.stdout[:400]


def test_and_pythons_regex_internals_never_reach_stderr(cornered):
    _, _, run = cornered
    r = run("[[:alpha:]].md")
    assert "FutureWarning" not in r.stderr, r.stderr[-200:]


def test_a_negated_class_does_not_match_a_separator_either(cornered):
    _, _, run = cornered
    out = run("[!w].md").stdout
    assert "w.md" in out and "a.md" not in out, out[:400]


# A newline is a legal character in a path segment, and `.` does not match
# one. The tool that hunts invisible characters does not get to be defeated
# by one in a directory name.
@pytest.fixture
def newline_dir(cornered):
    root, ledger, run = cornered
    nl = root / "d1" / "od\nnl"
    nl.mkdir()
    (nl / "b.md").write_text(DASH, encoding="utf-8")
    return run


def test_an_unanchored_pattern_matches_under_a_newline_directory(newline_dir):
    out = newline_dir("b.md").stdout
    assert "od\nnl/b.md" not in out and "long.md" in out, out[:400]


def test_a_trailing_globstar_reaches_past_a_newline_directory(newline_dir):
    out = newline_dir("d1/**").stdout
    assert ("od\nnl/b.md" not in out and "d1/b.md" not in out
            and "a.md" in out), out[:400]


def test_stacked_globstars_do_not_blow_up_the_walk(charck, cornered):
    # 11 stacked globstars took 23 seconds before they were collapsed at
    # compile time; the walk pays it per path.
    root, ledger, _ = cornered
    here = root / "deep"
    for i in range(22):
        here = here / ("l%d" % i)
    here.mkdir(parents=True)
    (here / "zzz.md").write_text(DASH, encoding="utf-8")
    ledger.write_text('[files]\nignore = ["%sx.md"]\n' % ("**/" * 11),
                      encoding="utf-8")
    try:
        r = charck("--config", ledger, "--no-append", "--no-gitignore", "-q",
                   root, timeout=20)
    except subprocess.TimeoutExpired:
        pytest.fail("timed out")
    assert r.returncode in (0, 1), r.stderr[-200:]
