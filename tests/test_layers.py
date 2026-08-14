"""Two ledgers, and which one decides.

Section S of the original suite: a local ledger overrides the global one per
character, the global one is never written to, and a local one is created in
the working directory when there is none in scope.
"""
import pytest


@pytest.fixture
def layers(global_ledger, tmp_path):
    """A global ledger, a project ledger, and a deeply nested working dir."""
    global_ledger.write_text(
        '[chars."U+2014"]\naction="replace"\nto=" - "\ncollapse=true\n'
        '[chars."U+2192"]\naction="replace"\nto="->"\n'
        '[chars."U+00B7"]\naction="replace"\nto="-"\n', encoding="utf-8")
    root = tmp_path / "proj"
    nested = root / "deep" / "nested"
    nested.mkdir(parents=True)
    (root / ".charck.toml").write_text(
        '[chars."U+2192"]\naction="replace"\nto="-"\n', encoding="utf-8")
    (nested / "doc.md").write_text("a — b, x → y, p · q\n", encoding="utf-8")
    return global_ledger, root, nested


@pytest.fixture
def fixed_doc(charck, layers):
    _, _, nested = layers
    charck("--fix", "-q", "doc.md", cwd=nested)
    return (nested / "doc.md").read_text(encoding="utf-8")


def test_global_decision_applies(fixed_doc):
    assert "a - b" in fixed_doc, repr(fixed_doc)


def test_local_overrides_global_for_its_character(fixed_doc):
    assert "x - y" in fixed_doc, repr(fixed_doc)


def test_unmentioned_characters_fall_through_to_global(fixed_doc):
    assert "p - q" in fixed_doc, repr(fixed_doc)


@pytest.fixture
def listing(charck, layers):
    _, _, nested = layers
    return charck("--list", cwd=nested).stdout


def test_list_marks_which_layer_decided_each_character(listing):
    assert "local" in listing and "global" in listing, listing[-300:]


def test_list_shows_the_overridden_character_as_local(listing):
    assert any("U+2192" in ln and "local" in ln
               for ln in listing.splitlines()), listing[-300:]


def test_list_shows_the_inherited_character_as_global(listing):
    assert any("U+2014" in ln and "global" in ln
               for ln in listing.splitlines()), listing[-300:]


def test_local_ledger_found_by_walking_up_from_a_nested_cwd(charck, layers):
    _, _, nested = layers
    (nested / "d2.md").write_text("x → y\n", encoding="utf-8")
    charck("--fix", "-q", "d2.md", cwd=nested)
    assert (nested / "d2.md").read_text(encoding="utf-8") == "x - y\n"


@pytest.fixture
def after_new_character(charck, layers):
    """A run turning up a character neither ledger has decided."""
    glob, root, nested = layers
    before = glob.read_bytes()
    (nested / "new.md").write_text("odd … char\n", encoding="utf-8")
    charck("-q", "new.md", cwd=nested)
    return glob, before, root / ".charck.toml"


def test_new_characters_appended_to_the_local_ledger(after_new_character):
    _, _, local = after_new_character
    assert "U+2026" in local.read_text(encoding="utf-8")


def test_global_ledger_untouched_when_a_local_one_exists(after_new_character):
    glob, before, _ = after_new_character
    assert glob.read_bytes() == before


@pytest.fixture
def solo(charck, layers, tmp_path):
    """A directory with no local ledger anywhere above it."""
    glob, _, _ = layers
    where = tmp_path / "solo"
    where.mkdir()
    (where / "x.md").write_text("„ quote\n", encoding="utf-8")
    before = glob.read_bytes()
    return where, glob, before, charck("-q", "x.md", cwd=where)


def test_global_ledger_never_appended_to_even_with_no_local_one(solo):
    _, glob, before, _ = solo
    assert glob.read_bytes() == before, glob.read_text(encoding="utf-8")[-160:]


def test_a_local_ledger_is_created_in_the_working_directory(solo):
    where, _, _, _ = solo
    assert (where / ".charck.toml").is_file(), \
        sorted(p.name for p in where.iterdir())


def test_the_new_character_landed_there(solo):
    where, _, _, _ = solo
    body = (where / ".charck.toml").read_text(encoding="utf-8")
    assert "U+201E" in body


def test_creating_a_ledger_is_announced_not_silent(solo):
    _, _, _, r = solo
    assert "created" in r.stdout and ".charck.toml" in r.stdout, \
        r.stdout[-200:]


def test_existing_ledger_is_reused_without_re_announcing(charck, solo):
    where, _, _, _ = solo
    r = charck("-q", "x.md", cwd=where)
    assert "created" not in r.stdout, r.stdout[-160:]


def test_local_ledger_not_rewritten_by_a_fix_over_its_own_tree(charck,
                                                               layers):
    _, root, _ = layers
    (root / "guard.md").write_text("x → y\n", encoding="utf-8")
    local = root / ".charck.toml"
    before = local.read_bytes()
    charck("--fix", "-q", ".", cwd=root)
    assert local.read_bytes() == before
