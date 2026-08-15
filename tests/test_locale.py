# SPDX-License-Identifier: MIT
# Copyright 2026, Lukáš Czerner <lukas@czerner.cz>
"""Which letters a run exempts, and where that answer comes from.

The command line, then a [locale] table, then the environment, then the Latin
script. A locale replaces the exempt set rather than adding to it, so Russian
does not carry Latin along with it, and an alphabet is recorded as the letters
themselves so that correcting it is one edit.

The floor underneath all of it is printable ASCII, which no locale and no
ledger entry can make reportable.
"""
import re

import pytest

# The ledger's own header comment talks about [locale], so a plain substring
# count would find it there. Only a table at the start of a line is one.
TABLE = re.compile(r"^\[locale\]", re.M)

CZECH = "Příliš žluťoučký kůň úpěl ďábelské ódy\n"
RUSSIAN = "Съешь же ещё этих мягких французских булок\n"
JAPANESE = "日本語のテキスト。「これ」も、ここでは普通\n"
GREEK = "Θέλει αρετή και τόλμη η ελευθερία\n"


@pytest.fixture
def lang(monkeypatch):
    """Set a locale variable for the run, the way a shell would."""
    def set_lang(value, var="LANG"):
        monkeypatch.setenv(var, value)
    return set_lang


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / ".charck.toml"


# ---- the environment -----------------------------------------------------

@pytest.mark.parametrize("locale,text", [
    ("cs_CZ.UTF-8", CZECH),
    ("ru_RU.UTF-8", RUSSIAN),
    ("ja_JP.UTF-8", JAPANESE),
    ("el_GR.UTF-8", GREEK),
])
def test_prose_in_the_environments_language_is_clean(charck, src, lang,
                                                     locale, text):
    lang(locale)
    r = charck(src("doc.txt", text))
    assert r.returncode == 0, r.stdout


def test_a_letter_from_another_language_is_still_reported(charck, src, lang):
    lang("cs_CZ.UTF-8")
    r = charck(src("doc.txt", "Łódź a Schrödinger\n"))
    assert "U+0141" in r.stdout and "U+00F6" in r.stdout, r.stdout


def test_a_locale_replaces_the_set_rather_than_adding_to_it(charck, src,
                                                            lang):
    # Russian exempts Cyrillic and nothing else: a French loanword in a
    # Russian file is as much a paste as any other.
    lang("ru_RU.UTF-8")
    r = charck(src("doc.txt", "кафе café\n"))
    assert "U+00E9" in r.stdout, r.stdout


def test_lc_all_outranks_lang(charck, src, lang):
    lang("ru_RU.UTF-8")
    lang("cs_CZ.UTF-8", "LC_ALL")
    r = charck(src("doc.txt", RUSSIAN))
    assert r.returncode == 1 and "CYRILLIC" in r.stdout, r.stdout


def test_a_coerced_lc_ctype_does_not_mask_the_language(charck, src, lang):
    # CPython injects LC_CTYPE=C.UTF-8 whenever the locale named is not one
    # the system has generated (PEP 538). Read in strict POSIX precedence it
    # outranks LANG, and a minimal container would scan Czech prose as though
    # no language had been named. A variable naming no language is skipped,
    # not taken as the answer.
    lang("cs_CZ.UTF-8")
    lang("C.UTF-8", "LC_CTYPE")
    r = charck(src("doc.txt", CZECH))
    assert r.returncode == 0, r.stdout


def test_the_script_modifier_picks_between_two_alphabets(charck, src, lang):
    # Serbian is written in both, and @latin is what says which is meant.
    lang("sr_RS@latin")
    r = charck(src("doc.txt", "Čačak i Đorđe\n"))
    assert r.returncode == 0, r.stdout


def test_a_language_written_in_ascii_exempts_nothing_beyond_it(charck, src,
                                                               lang):
    lang("en_US.UTF-8")
    r = charck(src("doc.txt", "a café\n"))
    assert "U+00E9" in r.stdout, r.stdout


@pytest.mark.parametrize("value", ["C", "POSIX", "C.UTF-8"])
def test_the_c_locale_is_read_as_no_language(charck, src, ledger, lang,
                                             value):
    # CPython coerces the C locale at startup and puts LC_CTYPE=C.UTF-8 into
    # its own environment (PEP 538), so this is what a run with nothing set
    # actually sees. Reading it as an unresolvable language would put a
    # complaint on every such run.
    lang(value)
    r = charck(src("doc.txt", "a café\n"))
    assert r.returncode == 0, r.stdout
    assert "no letters recorded" not in r.stdout, r.stdout
    assert not ledger.exists() or not TABLE.search(
        ledger.read_text(encoding="utf-8"))


def test_an_unknown_language_falls_back_and_says_so(charck, src, ledger,
                                                    lang):
    lang("xx_XX.UTF-8")
    r = charck(src("doc.txt", "a café\n"))
    # The Latin script, which is what charck exempted before it knew about
    # languages, and a line saying that is what happened.
    assert r.returncode == 0, r.stdout
    assert "no letters recorded for language" in r.stdout, r.stdout


def test_an_unknown_language_is_not_recorded(charck, src, ledger, lang):
    # Recording a guess we had no basis for would make it permanent. The next
    # run should ask again.
    lang("xx_XX.UTF-8")
    charck(src("doc.txt", "a — b\n"))
    assert not TABLE.search(ledger.read_text(encoding="utf-8"))


# ---- recording -----------------------------------------------------------

def test_an_alphabet_is_recorded_as_its_letters(charck, src, ledger, lang):
    lang("cs_CZ.UTF-8")
    charck(src("doc.txt", CZECH + "a — b\n"))
    body = ledger.read_text(encoding="utf-8")
    assert 'exempt = "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"' in body, body


def test_a_recorded_alphabet_repeats_no_letter(charck, src, ledger, lang):
    # The string the report tells people to edit. Turkish would otherwise
    # record `İ` twice and an ASCII `I` besides.
    lang("tr_TR.UTF-8")
    charck(src("doc.txt", "a — b\n"))
    line = [ln for ln in ledger.read_text(encoding="utf-8").splitlines()
            if ln.startswith("exempt")][0]
    letters = line.split('"')[1]
    assert len(letters) == len(set(letters)), letters
    assert not any(ch.isascii() for ch in letters), letters


def test_a_script_language_is_recorded_by_name(charck, src, ledger, lang):
    # 97668 Han ideographs is not a list, so the script keeps its name.
    lang("ja_JP.UTF-8")
    charck(src("doc.txt", JAPANESE + "a — b\n"))
    assert 'lang = "ja"' in ledger.read_text(encoding="utf-8")


def test_a_recorded_locale_outlives_the_environment(charck, src, lang):
    # The point of writing it down: the later run says the same thing under a
    # different $LANG, because it no longer asks.
    lang("cs_CZ.UTF-8")
    charck(src("dirty.txt", CZECH + "a — b\n"))
    lang("ru_RU.UTF-8")
    r = charck(src("clean.txt", CZECH))
    assert r.returncode == 0, r.stdout


def test_a_recorded_locale_is_not_written_twice(charck, src, ledger, lang):
    lang("cs_CZ.UTF-8")
    doc = src("doc.txt", CZECH + "a — b\n")
    charck(doc)
    charck(doc)
    assert len(TABLE.findall(ledger.read_text(encoding="utf-8"))) == 1


def test_no_append_records_no_locale(charck, src, ledger, lang):
    lang("cs_CZ.UTF-8")
    charck("--no-append", src("doc.txt", CZECH + "a — b\n"))
    assert not ledger.exists()


def test_a_clean_run_writes_nothing(charck, src, ledger, lang):
    # The locale rides along with a write that was happening anyway and never
    # causes one. A tree with nothing to report wrote nothing before this
    # feature and must write nothing now.
    lang("cs_CZ.UTF-8")
    r = charck(src("doc.txt", "plain ascii only\n"))
    assert r.returncode == 0 and not ledger.exists(), r.stdout


def test_a_clean_run_survives_a_read_only_directory(charck, src, restore_mode,
                                                    tmp_path, lang):
    # Recording the locale must not turn a green build gate into exit 2 on a
    # read-only checkout.
    lang("cs_CZ.UTF-8")
    doc = src("doc.txt", "plain ascii only\n")
    restore_mode(tmp_path, 0o500)
    r = charck(doc)
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_a_ledger_locale_is_left_alone(charck, src, ledger, lang):
    # An answer already written down needs no help from the environment.
    ledger.write_text('[locale]\nlang = "ru"\n', encoding="utf-8")
    lang("cs_CZ.UTF-8")
    charck(src("doc.txt", RUSSIAN))
    body = ledger.read_text(encoding="utf-8")
    assert len(TABLE.findall(body)) == 1 and "exempt" not in body, body


# ---- the command line ----------------------------------------------------

def test_lang_overrides_the_environment(charck, src, lang):
    lang("ru_RU.UTF-8")
    r = charck("--lang", "cs_CZ", src("doc.txt", CZECH))
    assert r.returncode == 0, r.stdout


def test_lang_overrides_the_ledger(charck, src, ledger):
    ledger.write_text('[locale]\nlang = "cs"\n', encoding="utf-8")
    r = charck("--lang", "ru", src("doc.txt", CZECH))
    assert "U+0159" in r.stdout, r.stdout


def test_lang_accepts_a_script_name(charck, src):
    r = charck("--lang", "cyrillic", src("doc.txt", RUSSIAN))
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("flag", [("--no-lang",), ("--lang", "none")])
def test_nothing_beyond_ascii_is_exempt(charck, src, flag):
    r = charck(*flag, src("doc.txt", "café\n"))
    assert "U+00E9" in r.stdout, r.stdout


def test_no_lang_overrides_a_recorded_locale(charck, src, ledger):
    ledger.write_text('[locale]\nexempt = "é"\n', encoding="utf-8")
    r = charck("--no-lang", src("doc.txt", "café\n"))
    assert "U+00E9" in r.stdout, r.stdout


def test_ascii_stays_exempt_under_no_lang(charck, src):
    # --no-lang reaches the floor and stops there.
    r = charck("--no-lang", src("doc.txt", "plain ascii\n"))
    assert r.returncode == 0, r.stdout


def test_list_says_what_is_exempt_and_where_it_came_from(charck, lang):
    lang("cs_CZ.UTF-8")
    r = charck("--list")
    assert "exempt:" in r.stdout and "$LANG=cs_CZ.UTF-8" in r.stdout, r.stdout


# ---- layering ------------------------------------------------------------

def test_a_local_locale_replaces_the_global_one(charck, src, global_ledger,
                                                cfg):
    # Not merged: a project that names a language means that language, and
    # merging would leave it unable to be stricter than the global ledger.
    global_ledger.write_text('[locale]\nlang = "ru"\n', encoding="utf-8")
    cfg(".charck.toml", '[locale]\nlang = "cs"\n')
    r = charck(src("doc.txt", RUSSIAN))
    assert "CYRILLIC" in r.stdout, r.stdout


def test_case_is_exempt_both_ways(charck, src, cfg):
    # Nobody means a letter in one case only.
    cfg(".charck.toml", '[locale]\nexempt = "áč"\n')
    r = charck(src("doc.txt", "áČ\n"))
    assert r.returncode == 0, r.stdout


def test_a_hand_added_letter_stops_being_a_finding(charck, src, cfg):
    # The workflow the recorded form exists for: one edit, not one table per
    # foreign letter.
    cfg(".charck.toml", '[locale]\nexempt = "áčďéěíňóřšťúůýžö"\n')
    r = charck(src("doc.txt", "Schrödinger ódy\n"))
    assert r.returncode == 0, r.stdout


# ---- a ledger entry outranks the locale ----------------------------------

def test_an_undecided_entry_does_not_un_exempt(charck, src, cfg):
    # The tool writes an undecided entry for every character it has ever seen,
    # so an entry carries no intent until an action is filled in. If one
    # un-exempted, a letter could never be silenced by adding it to [locale]
    # exempt, and the report would print that advice having just watched it
    # fail.
    cfg(".charck.toml",
        '[locale]\nexempt = "áö"\n[chars."U+00F6"]\naction = ""\n')
    r = charck(src("doc.txt", "Schrödinger á\n"))
    assert r.returncode == 0, r.stdout


def test_adding_a_letter_to_exempt_silences_it_for_good(charck, src, ledger,
                                                        lang):
    # End to end, the workflow the recorded form exists for: the first run
    # asks about a foreign letter, the answer is one edit, and the next run is
    # clean rather than repeating the same request forever.
    lang("cs_CZ.UTF-8")
    doc = src("doc.txt", "Schrödinger a jeho kočka\n")
    assert charck(doc).returncode == 1
    body = ledger.read_text(encoding="utf-8")
    ledger.write_text(body.replace('ÝŽ"', 'ÝŽöÖ"'), encoding="utf-8")
    r = charck(doc)
    assert r.returncode == 0, r.stdout


def test_an_entry_can_rewrite_a_letter_the_locale_exempts(charck, src, cfg):
    # Report and rewrite must agree: a decision that shows in the report is
    # one --fix may act on, locale or no locale.
    cfg(".charck.toml",
        '[locale]\nlang = "cs"\n'
        '[chars."U+00E1"]\naction = "replace"\nto = "a"\n')
    doc = src("doc.txt", "ódy á\n")
    charck("--fix", "-q", doc)
    assert doc.read_text(encoding="utf-8") == "ódy a\n"


def test_an_exempt_letter_with_no_entry_is_never_rewritten(charck, src, cfg):
    cfg(".charck.toml", '[locale]\nlang = "cs"\n')
    doc = src("doc.txt", CZECH)
    charck("--fix", "-q", doc)
    assert doc.read_text(encoding="utf-8") == CZECH


# ---- the floor -----------------------------------------------------------

@pytest.mark.parametrize("action", ["delete", 'replace"\nto = "x'])
def test_an_entry_acting_on_printable_ascii_is_refused(charck, src, cfg,
                                                       action):
    c = cfg("only.toml", '[chars."U+0041"]\naction = "%s"\n' % action)
    r = charck("--config", c, src("doc.txt"))
    assert r.returncode == 2 and "never reported" in r.stderr, r.stderr


def test_an_ignore_entry_on_printable_ascii_reports_nothing(charck, src, cfg):
    # The floor is absolute. An entry brings a character back into the report
    # only where the locale had taken it out, never here.
    c = cfg("only.toml", '[chars."U+0041"]\naction = "ignore"\n')
    r = charck("--config", c, "-v", src("doc.txt", "AAA plain\n"))
    assert r.returncode == 0 and "U+0041" not in r.stdout, r.stdout


@pytest.mark.parametrize("locale", ["cs_CZ.UTF-8", "ja_JP.UTF-8", "C"])
def test_invisible_whitespace_survives_every_locale(charck, src, lang,
                                                    locale):
    # U+3000 is ordinary in Japanese typesetting and deliberately still
    # reported: invisible whitespace is the thing this tool exists to find.
    lang(locale)
    r = charck(src("doc.txt", "a　b​c\n"))
    assert "U+3000" in r.stdout and "U+200B" in r.stdout, r.stdout


@pytest.mark.parametrize("locale,text,code", [
    # Category Lo, and they render as blank. They carry their script's name,
    # so a script rule hands them back unless it is stopped; these being
    # letters by category and nothing by appearance is the reason charck
    # reports by exemption rather than by category in the first place.
    ("ko", "한국ㅤ어\n", "U+3164"),
    ("ko", "aᅟb\n", "U+115F"),
    ("ko", "aᅠb\n", "U+1160"),
    ("km", "ខ឴្មែរ\n", "U+17B4"),
])
def test_a_blank_letter_is_reported_under_its_own_script(charck, src, locale,
                                                         text, code):
    r = charck("--no-append", "--lang", locale, src("doc.txt", text))
    assert code in r.stdout, r.stdout


@pytest.mark.parametrize("locale,text", [
    ("hi", "यह एक वाक्य है।\n"),          # U+0964 danda ends every sentence
    ("am", "ሰላም ልዑል።\n"),                # U+1362 Ethiopic full stop
    ("ar", "مرحبا، كيف حالك؟ ١٢٣\n"),     # comma, question mark, digits
    ("th", "ทดสอบ ๑๒๓\n"),                # Thai digits
    ("hy", "Բարի Ձեզ։\n"),                # U+0589 Armenian full stop
    ("yi", "אַ גוטן טאָג װײַל\n"),           # Yiddish ligature letters
])
def test_a_script_carries_its_own_punctuation_and_digits(charck, src, locale,
                                                         text):
    # The reasoning that keeps CJK full stops out of the report applies to
    # every other script. Without it a Hindi or Amharic run flags every
    # sentence it reads.
    r = charck("--no-append", "--lang", locale, src("doc.txt", text))
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("locale,text", [
    ("ca", "La qüestió de les aigües és difícil\n"),
    ("be", "Гэта ёсць цяжкае пытанне\n"),
    ("es", "¿Cómo estás? ¡Muy bien!\n"),
])
def test_ordinary_prose_is_clean_in_these_languages(charck, src, locale,
                                                    text):
    r = charck("--no-append", "--lang", locale, src("doc.txt", text))
    assert r.returncode == 0, r.stdout


def test_a_modifier_survives_a_codeset(charck, src):
    # glibc spells a locale language[_territory][.codeset][@modifier], so
    # taking the codeset off first swallows @latin with it and resolves
    # Serbian to Cyrillic. This is the form `locale -a` prints.
    doc = src("doc.txt", "Ovo je Četvrtak i đak\n")
    r = charck("--no-append", "--lang", "sr_RS.UTF-8@latin", doc)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("value", ["C", "POSIX"])
def test_a_named_c_locale_is_not_called_unknown(charck, src, value):
    # env_locale skips these; --lang and a ledger must agree with it rather
    # than complain about a value the tool elsewhere reads as silence.
    r = charck("--no-append", "--lang", value, src("doc.txt", "a café\n"))
    assert r.returncode == 0 and "no letters recorded" not in r.stdout


def test_an_empty_lang_is_refused(charck, src):
    # Every other unusable value says so; this one used to behave as though
    # the flag were absent.
    r = charck("--lang", "", src("doc.txt"))
    assert r.returncode == 2 and "--lang needs a language" in r.stderr


@pytest.mark.parametrize("locale,text,code", [
    ("cs", "ﬁle\n", "U+FB01"),      # the PDF paste the rule was written for
    ("hy", "Բարև\n", "U+0587"),     # decomposes; a normaliser expands it
])
def test_a_compatibility_ligature_is_reported(charck, src, locale, text,
                                              code):
    r = charck("--no-append", "--lang", locale, src("doc.txt", text))
    assert code in r.stdout, r.stdout


@pytest.mark.parametrize("locale,text", [
    ("vi", "cœur\n"),                 # no decomposition: a letter, not a paste
    ("yi", "װײ\n"),
])
def test_a_ligature_named_letter_is_not_a_paste_artifact(charck, src, locale,
                                                         text):
    # Matching on the name alone flagged every Yiddish text, `װ ױ ײ` being
    # named ligature while having no compatibility mapping at all.
    r = charck("--no-append", "--lang", locale, src("doc.txt", text))
    assert r.returncode == 0, r.stdout


def test_a_bom_is_reported_under_a_script_locale(charck, src, lang):
    lang("ja_JP.UTF-8")
    r = charck(src("doc.txt", "﻿日本\n"))
    assert "U+FEFF" in r.stdout, r.stdout


# ---- a malformed table ---------------------------------------------------

@pytest.mark.parametrize("body,expected", [
    ("[locale]\n", "decides nothing"),
    ('[locale]\nlanguage = "cs"\n', "has no `language` key"),
    ("[locale]\nexempt = 5\n", "must be a quoted string"),
    ("[locale]\nlang = true\n", "must be a quoted string"),
    ('locale = "cs"\n', "must be a table"),
])
def test_a_malformed_locale_table_is_refused(charck, src, cfg, body,
                                             expected):
    c = cfg("only.toml", body)
    r = charck("--config", c, src("doc.txt"))
    assert r.returncode == 2 and expected in r.stderr, r.stderr
