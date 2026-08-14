"""Somebody else's ignore file, read the way git reads it.

Sections V and Y of the original suite: a .gitignore applies only inside a
work tree, a deeper one overrides a shallower one, .git is never walked by
any spelling, an unusable line is not fatal because the file is not ours,
and the parsing corners git itself has.
"""
import pytest

from conftest import DASH, skip_if_root

REPO_FILES = ["top.md", "skip.md", "sub/keep.md", "sub/hide.md",
              "sub/deeper/x.md", ".git/info/"]


@pytest.fixture
def plain_ledger(cfg):
    """An empty ledger: these tests are about the files, not the decisions."""
    return cfg("v.toml")


@pytest.fixture
def scan(charck, plain_ledger):
    def out(*args):
        return charck("--config", plain_ledger, "--no-append", *args).stdout
    return out


@pytest.fixture
def repo(tree):
    """A work tree with a .gitignore at its root."""
    root = tree("repo", REPO_FILES)
    (root / ".gitignore").write_text("skip.md\nhide.md\n", encoding="utf-8")
    return root


def test_a_repo_root_gitignore_is_applied(repo, scan):
    out = scan(repo)
    assert "skip.md" not in out and "sub/hide.md" not in out, out[:400]


def test_and_everything_else_is_scanned(repo, scan):
    out = scan(repo)
    assert ("top.md" in out and "sub/keep.md" in out
            and "deeper/x.md" in out), out[:400]


def test_a_gitignore_above_the_walked_directory_still_applies(repo, scan):
    assert "sub/hide.md" not in scan(repo / "sub")


def test_no_gitignore_stops_reading_them(repo, scan):
    assert "skip.md" in scan("--no-gitignore", repo)


def test_a_deeper_gitignore_overrides_a_shallower_one(repo, scan):
    (repo / "sub" / ".gitignore").write_text("!hide.md\nkeep.md\n",
                                             encoding="utf-8")
    out = scan(repo)
    assert "sub/hide.md" in out and "sub/keep.md" not in out, out[:400]


def test_git_info_exclude_is_read(repo, scan):
    (repo / ".git" / "info" / "exclude").write_text("top.md\n",
                                                    encoding="utf-8")
    assert "top.md" not in scan(repo)


def test_a_ledger_pattern_has_the_last_word_over_gitignore(charck, cfg, repo):
    ledger = cfg("v2.toml", '[files]\nignore = ["!skip.md"]\n')
    assert "skip.md" in charck("--config", ledger, "--no-append", repo).stdout


@pytest.fixture
def worktree(tree):
    """A work tree whose .git is a file, as a worktree or submodule has.

    The em dash in it is what makes the checks mean something: without a
    reportable character, silence would prove nothing.
    """
    root = tree("wtree", ["hide.md", "keep.md"])
    (root / ".git").write_text("gitdir: /nowhere — x\n", encoding="utf-8")
    (root / ".gitignore").write_text("hide.md\n", encoding="utf-8")
    return root


def test_a_git_file_marks_a_work_tree_as_a_git_directory_does(worktree, scan):
    out = scan(worktree)
    assert "hide.md" not in out and "keep.md" in out, out[:400]


def test_a_git_file_is_never_scanned_even_under_no_ignore(worktree, scan):
    out = scan("--no-ignore", "-v", worktree)
    assert "gitdir" not in out, out[:400]


def test_the_same_bytes_under_any_other_name_are_reported(tree, scan):
    control = tree("wcontrol")
    (control / "notgit").write_text("gitdir: /nowhere — x\n",
                                    encoding="utf-8")
    assert "gitdir" in scan("--no-ignore", control)


@pytest.fixture
def gitesc(cfg, tree):
    """A repository, and three ways of pointing a walk into its .git."""
    root = tree("gitesc", ["repo/.git/HEAD"])
    (root / "repo" / "notes.md").symlink_to(".git/HEAD")
    (root / "dotgit").symlink_to(root / "repo" / ".git")  # a name that hides
    (root / "alias").symlink_to(root / "repo")            # a linked parent
    ledger = cfg("gitesc.toml",
                 '[chars."U+2014"]\naction = "replace"\nto = "-"\n')
    return root, ledger


def test_a_symlink_leading_back_into_git_is_not_followed(charck, gitesc):
    # write_atomic follows a link to the real file, so an unguarded walk
    # would rewrite a loose ref while the report named the link.
    root, ledger = gitesc
    charck("--config", ledger, "--no-append", "--fix", "-q", root)
    head = root / "repo" / ".git" / "HEAD"
    assert head.read_text(encoding="utf-8") == DASH, \
        repr(head.read_text(encoding="utf-8"))


@pytest.mark.parametrize("arg", ["repo/.git", "dotgit", "alias/.git"],
                         ids=["named directly", "through a symlink to it",
                              "through a symlinked parent"])
def test_walking_git_is_refused_with_exit_2(charck, gitesc, arg):
    root, ledger = gitesc
    r = charck("--config", ledger, "--no-append", "--fix", "-q", root / arg)
    assert r.returncode == 2, "exit=%d %s" % (r.returncode, r.stderr[-120:])
    head = root / "repo" / ".git" / "HEAD"
    assert head.read_text(encoding="utf-8") == DASH, "and it was rewritten"


def test_a_single_file_inside_git_named_directly_is_still_scanned(gitesc,
                                                                  scan):
    root, _ = gitesc
    assert "HEAD" in scan(root / "repo" / ".git" / "HEAD")


@pytest.fixture
def norepo(tree, outside_any_repo):
    """A .gitignore with no work tree above it, and a repository below."""
    root = tree("norepo", ["hide.md", "inner/hide.md", "inner/.git/"])
    (root / ".gitignore").write_text("hide.md\n", encoding="utf-8")
    (root / "inner" / ".gitignore").write_text("hide.md\n", encoding="utf-8")
    return root


def test_a_gitignore_outside_any_work_tree_is_not_read(norepo, scan):
    out = scan(norepo)
    assert "%s/hide.md" % norepo in out, out[:300]


def test_but_a_repository_below_the_walk_root_is_still_honoured(norepo, scan):
    out = scan(norepo)
    assert "inner/hide.md" not in out, out[:300]


@pytest.fixture
def lenient(tree):
    """Someone else's file, with one line we cannot compile in it."""
    root = tree("lenient", ["hide.md", "ok.md", ".git/"])
    (root / ".gitignore").write_text("../oops\nhide.md\n", encoding="utf-8")
    return root


def test_an_unusable_gitignore_line_does_not_stop_the_run(charck,
                                                          plain_ledger,
                                                          lenient):
    r = charck("--config", plain_ledger, "--no-append", lenient)
    assert r.returncode == 1 and "ok.md" in r.stdout, \
        "exit=%d %s" % (r.returncode, r.stderr[-160:])


def test_and_the_lines_around_it_still_apply(lenient, scan):
    out = scan(lenient)
    assert "hide.md" not in out, out[:300]


def test_and_v_says_which_line_could_not_be_used(lenient, scan):
    assert "unusable pattern" in scan("-v", lenient)


# ---- a .gitignore is read the way git reads it ----

@pytest.fixture
def gitignored(tree, scan):
    root = tree("readgi", ["a.md", "b.md", ".git/"])
    (root / "a ").write_text(DASH, encoding="utf-8")  # trailing space

    def run(body, *args):
        (root / ".gitignore").write_bytes(body)
        return root, scan(*args, root)

    return run


# The tool exists to find invisible characters; one in a .gitignore used to
# make its first pattern silently match nothing.
def test_a_bom_does_not_disarm_the_first_pattern(gitignored):
    _, out = gitignored(b"\xef\xbb\xbfa.md\n")
    assert "a.md" not in out and "b.md" in out, out[:400]


def test_crlf_line_endings_are_handled(gitignored):
    _, out = gitignored(b"a.md\r\nb.md\r\n")
    assert "a.md" not in out and "b.md" not in out, out[:400]


def test_an_escaped_trailing_space_is_kept(gitignored):
    root, out = gitignored(b"a\\ \n")
    assert "%s/a \n" % root not in out and "a.md" in out, out[:400]


def test_unescaped_trailing_spaces_are_dropped(gitignored):
    _, out = gitignored(b"a.md   \n")
    assert "a.md" not in out, out[:400]


def test_comments_and_blank_lines_are_skipped(gitignored):
    _, out = gitignored(b"#a.md\n\n   \nb.md\n")
    assert "a.md" in out and "b.md" not in out, out[:400]


@pytest.fixture
def unreadable_gitignore(charck, plain_ledger, tree, restore_mode):
    root = tree("readgi", ["a.md", "b.md", ".git/"])
    (root / ".gitignore").write_bytes(b"a.md\n")
    restore_mode(root / ".gitignore", 0o000)
    return charck("--config", plain_ledger, "--no-append", "-v", root)


@skip_if_root
def test_an_unreadable_gitignore_is_reported(unreadable_gitignore):
    r = unreadable_gitignore
    assert "unreadable" in r.stdout, \
        [ln for ln in r.stdout.splitlines()
         if "unreadable" in ln or "scanned" in ln]


@skip_if_root
def test_and_the_run_still_finishes_normally(unreadable_gitignore):
    assert unreadable_gitignore.returncode == 1, \
        "exit=%d" % unreadable_gitignore.returncode
