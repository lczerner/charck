#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright 2026, Lukáš Czerner <lukas@czerner.cz>
"""charck - check text files for non-ASCII characters, and repair them.

Reports every character that is not printable ASCII, with codepoint, name,
count and exact position. Exempt from that are the letters your language
writes with, so ordinary prose is not flagged on every run; ligatures and
fullwidth forms are still reported, being paste artifacts rather than letters.

Which letters those are comes from the locale: `cs_CZ.UTF-8` exempts the
fifteen letters Czech uses, and no others, so a Polish `l` with a stroke in
Czech prose is still a finding. Languages an alphabet cannot describe get
their whole script instead: Japanese is kana and 97668 Han ideographs, which
is not a list anyone can write down. The order of preference is

  --lang / --no-lang             this run only
  [locale] in the ledger         local layer replaces the global one
  $LC_ALL, $LC_CTYPE, $LANG      recorded to the ledger on first use
  the Latin script               what charck exempted before it asked

A locale read from the environment is written into the ledger as the run
goes, so later runs no longer depend on a variable. An alphabet is recorded as
the letters themselves:

  [locale]
  exempt = "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"

which is also how you correct it. Add the letters of a foreign name you would
rather not be told about again, and the tool's own table stops mattering.
`lang = "none"` exempts nothing beyond ASCII, and so does --no-lang.

A character you have decided to delete or replace is reported whatever the
locale says, so that --fix never changes bytes the report did not show. An
undecided entry does not do that: the tool writes one for every character it
has ever seen, so an entry carries no intent until you fill in an action.

What to do about each character is recorded in a ledger. Detection appends
newly-seen characters there as undecided; you fill in delete / replace /
ignore; later runs apply those decisions.

There are two ledgers, and they layer per character:

  ~/.config/charck/charck.toml   your decisions, applied everywhere
  ./.charck.toml                 this project's exceptions, found by walking up
                                 from the working directory

A character decided in the local ledger uses that decision; every other
character falls back to the global one. So a project can disagree about a
single character without restating the rest.

The global ledger is never written to. It is yours to edit by hand, and
nothing the tool discovers is added to it automatically. Newly-seen characters
go to the local ledger, which is created in the working directory if none is
in scope. `--config PATH` ignores both layers and reads and appends to exactly
that file.

What a walk leaves alone is recorded in the same ledger, in gitignore syntax:

  [files]
  ignore = ["build/", "*.min.js", "!keep.md"]

Those patterns are applied after a set of built-in ones (dot-files,
node_modules and the usual build output) which a `!` pattern can override, and
after any .gitignore in scope, which is read unless --no-gitignore says
otherwise. A path named directly on the command line is never ignored, and
.git is never walked into whatever the patterns say.

`--exclude PATTERN` adds one more pattern, in the same syntax, from the
command line. It outranks every recorded one, and it is written to the ledger
as the run goes, so a first run over a tree can leave out what it should and
every later run already knows. --no-append applies it to this run only.

Caveats worth knowing:

  Cn (unassigned) is Unicode-version-dependent. A character assigned in a
  Unicode release newer than this Python's will read as unassigned here. Such
  findings are labelled so a false positive is visible before you decide to
  delete them.

  Co (private use, U+E000-F8FF) is where Nerd Font and Powerline glyphs live. A
  "delete" decision on those would strip prompt icons from a shell config.

  --fix replaces files atomically, which breaks hardlinks: a rewritten file
  gets a new inode, so any other name that shared the old one keeps the old
  content. Extended attributes and ACLs are not carried over either. Symlinks
  are followed, so the real file is rewritten and the link is left intact.
"""

import argparse
import os
import re
import sys
import tempfile
import tomllib
import unicodedata
from pathlib import Path

try:
    import fcntl
except ImportError:                                    # pragma: no cover
    fcntl = None

SCRIPT = Path(__file__).resolve()
UNICODE_VERSION = unicodedata.unidata_version

# Two layers. The global ledger holds decisions that hold everywhere; a
# project may override individual characters, and only those, in a local
# ledger found by walking up from the working directory. Deliberately not
# "beside the script": an installed copy lives in site-packages, which may be
# read-only and is replaced on upgrade, which would silently discard every
# recorded decision.
LOCAL_NAME = ".charck.toml"


def xdg_config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def global_config():
    return xdg_config_home() / "charck" / "charck.toml"


def find_local_config(start=None):
    """Nearest .charck.toml at or above `start`, or None."""
    try:
        here = Path(start or Path.cwd()).resolve()
    except OSError:
        return None
    for directory in (here, *here.parents):
        candidate = directory / LOCAL_NAME
        if candidate.is_file():
            return candidate
    return None


def resolve_layers(explicit=None):
    """Return ([(layer, base)] lowest priority first, path-appended-to).

    `base` is the directory an anchored ignore pattern in that layer is
    relative to, as gitignore anchors to the directory of the file it is
    written in. The global ledger has no such directory, and None says so.

    An explicit --config replaces the whole stack, so scripted and test runs
    get exactly the file they name and nothing else.
    """
    if explicit is not None:
        path = Path(explicit)
        return [(path, path.resolve().parent)], path
    layers = []
    glob = global_config()
    if glob.is_file():
        layers.append((glob, None))
    local = find_local_config()
    if local is not None:
        layers.append((local, local.parent))
    # New characters are only ever appended to a local ledger, never to the
    # global one: that file is yours to edit by hand, and the tool must not
    # grow it behind your back. With no local ledger in scope, one is created
    # in the working directory rather than falling back to the global.
    return layers, (local if local is not None else Path.cwd() / LOCAL_NAME)


def merge_layers(layers):
    """Later layers override earlier ones per character, not wholesale.

    Returns (config, source, groups, locale): source[ch] is the layer that
    decided it, and groups are the layers' ignore rules in the same order, so
    a local `!` pattern has the last word just as a local decision does.

    [locale] is the one thing that does not layer per item. A local table
    replaces the global one entirely, because the set is the unit here: a
    project declaring a language means that language, and merging would make
    it impossible to be stricter than the global ledger.
    """
    config, source, groups, locale = {}, {}, [], None
    for path, base in layers:
        chars, patterns, table = load_config(path, base)
        for ch, entry in chars.items():
            config[ch] = entry
            source[ch] = path
        if table is not None:
            locale = dict(table, path=path)
        if patterns:
            label = "global" if base is None else (
                "local" if path.name == LOCAL_NAME else "config")
            groups.append(rule_group(patterns, base, path, label))
    return config, source, groups, locale


# This script and the ledger it is reading describe the characters we hunt, so
# rewriting them would corrupt the tool itself. Scanned and reported, but never
# rewritten unless named directly on the command line. The config file actually
# in use is added to this set at runtime.
SELF_FILES = {SCRIPT.name}

ACTIONS = ("", "delete", "replace", "ignore")
KEY_RE = re.compile(r"^U\+[0-9A-F]{4,6}$")
TABLE_RE = re.compile(r'^\[chars\."(U\+[0-9A-Fa-f]{4,6})"\]', re.M)
# Both spellings a [locale] table can take, so a second one is never appended
# beside a hand-written dotted key. A duplicate would be a TOML error, and the
# next run would exit 2 on a ledger this one wrote.
LOCALE_RE = re.compile(r"^[ \t]*(\[locale\]|locale[ \t]*\.)", re.M)
CONTEXT = 40

HEADER = """\
# charck.toml - decision ledger.
#
# New characters are appended automatically as they are discovered. Fill in
# `action` for each; entries left undecided are reported but never modified.
#
#   action = ""         undecided - reported, never modified
#            "delete"   remove entirely
#            "replace"  substitute `to`
#            "ignore"   intentionally keep, drop from future reports
#
# `collapse = true` on a replace also eats spaces/tabs either side of the
# character, so "a - b" does not become "a  -  b". Indentation at the start
# of a line is never eaten.
#
# The letters your language writes with go in a [locale] table, and are never
# reported. They are recorded from $LANG on a first run, and are yours to
# edit; add a letter and it stops being a finding.
#
#   [locale]
#   exempt = "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
#
# A character you have decided to delete or replace above is reported whatever
# [locale] says, so that --fix never changes what the report did not show.
#
# Paths a walk should leave alone go in a [files] table, in gitignore syntax:
#
#   [files]
#   ignore = ["build/", "*.min.js", "!keep.md"]
#
# .gitignore is honoured too, and `charck --exclude PATTERN` adds a pattern to
# that array as it runs. A path named directly on the command line is scanned
# whatever the patterns say.

"""


def plural(n, word, suffix="s"):
    return "%d %s%s" % (n, word, "" if n == 1 else suffix)


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def is_ascii_exempt(ch):
    """The floor: printable ASCII, tab and newline, never reported whatever
    the locale says.

    A ledger entry acting on one of these is a fatal config error, because the
    report could never have shown it. Everything above this floor is the
    locale's business, and a ledger entry there brings the character back into
    the report rather than being refused.
    """
    return ch == "\n" or ch == "\t" or "\x20" <= ch <= "\x7e"


def key_of(ch):
    return "U+%04X" % ord(ch)


def name_of(ch):
    cat = unicodedata.category(ch)
    name = unicodedata.name(ch, "")
    if name:
        return name
    if cat == "Cc":
        return "<control>"
    if cat == "Co":
        return "<private use>"
    if cat == "Cs":
        return "<surrogate>"
    return "<unassigned, Unicode %s>" % UNICODE_VERSION


# Suggestions are comments only. The script never acts on one; it acts solely
# on an `action` you have written yourself.
def suggest(ch):
    cp, cat = ord(ch), unicodedata.category(ch)
    typography = {
        0x2014: ('action = "replace", to = " - ", collapse = true', None),
        0x2013: ('action = "replace", to = "-", collapse = true', None),
        0x2011: ('action = "replace", to = "-"', None),
        0x2018: ("action = \"replace\", to = \"'\"", None),
        0x2019: ("action = \"replace\", to = \"'\"", None),
        0x201C: ('action = "replace", to = "\\""', None),
        0x201D: ('action = "replace", to = "\\""', None),
        0x2026: ('action = "replace", to = "..."', None),
        0x2032: ("action = \"replace\", to = \"'\"", None),
        0x2033: ('action = "replace", to = "\\""', None),
        0x00B7: ('action = "replace", to = "-"', None),
        0x2192: ('action = "replace", to = "->"', None),
        0x2190: ('action = "replace", to = "<-"', None),
        0xFB01: ('action = "replace", to = "fi"',
                 "ligature, usually a PDF paste"),
        0xFB02: ('action = "replace", to = "fl"',
                 "ligature, usually a PDF paste"),
    }
    if cp in typography:
        return typography[cp]
    if cp == 0x000D:
        return ('action = "delete"',
                "stray CR; a CR that ends a CRLF line is never touched")
    if cp == 0xFFFD:
        return ('action = "delete"',
                "mojibake marker - evidence of prior data loss, "
                "inspect the source")
    if cp == 0xFEFF:
        return ('action = "delete"', "byte order mark")
    if cat == "Zs":
        return ('action = "replace", to = " "',
                "deleting would glue the surrounding words together")
    if cat in ("Zl", "Zp") or cp == 0x0085:
        return ('action = "replace", to = "\\n"', "this is a line break")
    if cat == "Cc":
        return ('action = "delete"', "control character")
    if cat == "Cf":
        return ('action = "delete"', "zero-width")
    if cat == "Cs":
        return ('action = "delete"', "lone surrogate")
    if cat == "Cn":
        return ('action = "delete"',
                "unassigned in Unicode %s - may be newer than this Python"
                % UNICODE_VERSION)
    if cat == "Co":
        return ('action = "delete"',
                "private use - Nerd Font / Powerline glyphs live here")
    if "VARIATION SELECTOR" in unicodedata.name(ch, ""):
        return ('action = "delete"',
                "invisible; this range is the text-smuggling vector")
    if "FILLER" in unicodedata.name(ch, "") or cp == 0x2800:
        return ('action = "delete"', "renders as blank")
    if cat == "Mn":
        return ('action = "delete"',
                "combining mark - text may be NFD-decomposed")
    return (None, None)


# --------------------------------------------------------------------------
# locale
# --------------------------------------------------------------------------

# The letters a language actually uses, beyond ASCII. Lower case only; the
# upper case half is derived. Being exact is the point: a Czech ledger that
# exempted the whole Latin script would accept a Polish `ł` and a German `ß`
# in Czech prose, which are precisely the pastes worth seeing.
#
# A language whose letters cannot be written down goes in SCRIPT_LANGS below,
# not here. So does one where a letter list would be a lie: Vietnamese has 134
# precomposed forms, and a hand-typed list that missed three would be worse
# than the script rule it falls back to.
ALPHABETS = {
    "af": "áèéêëíîïôóúû",
    "ca": "àçéèíïóòúü·",
    "cs": "áčďéěíňóřšťúůýž",
    "da": "æøå",
    "de": "äöüß",
    # The inverted marks open a question and an exclamation in Spanish, and
    # are as obligatory as any letter. Punctuation earns a place here where a
    # language cannot be written without it, as the Catalan middle dot does.
    "es": "áéíñóúü¿¡",
    "et": "äöõüšž",
    # Written in plain ASCII, and saying so is the point: an accented letter
    # in English prose is a paste worth seeing, not an everyday letter.
    "en": "",
    "eu": "ñ",
    "fi": "äöåšž",
    "fo": "áðíóúýæø",
    "fr": "àâæçéèêëîïôùûüÿœ",
    "ga": "áéíóú",
    "gl": "áéíóúüñ",
    "hr": "čćđšž",
    "hu": "áéíóöőúüű",
    "id": "",
    "is": "áðéíóúýþæö",
    "it": "àèéìíîòóùú",
    "lt": "ąčęėįšųūž",
    "lv": "āčēģīķļņšūž",
    "ms": "",
    "nb": "æøå",
    "nl": "áàäéèëíìïóòöúùü",
    "nn": "æøå",
    "no": "æøå",
    "pl": "ąćęłńóśźż",
    "pt": "àáâãçéêíóôõú",
    "ro": "ăâîșțşţ",
    "sk": "áäčďéíĺľňóôŕšťúýž",
    "sl": "čšžđ",
    "sv": "åäö",
    "sw": "",
    # `İ` by hand: Python's casing is not locale-aware, so expanding `i` here
    # would give the ASCII `I` and never the dotted capital Turkish uses.
    "az": "çəğıöşüİ",
    "tr": "çğıöşüİ",
    # Cyrillic. Serbian and Azerbaijani are written in two scripts, and the
    # @latin modifier on a locale says which, so both spellings are keys.
    # `ё` is obligatory in Belarusian, unlike Russian, where it is optional.
    "be": "абвгдеёжзійклмнопрстуўфхцчшыьэюя",
    "bg": "абвгдежзийклмнопрстуфхцчшщъьюя",
    "kk": "абвгғдеёжзийкқлмнңоөпрстуұүфхһцчшщъыіьэюя",
    "ky": "абвгдеёжзийклмнңоөпрстуүфхцчшщъыьэюя",
    "mk": "абвгдѓежзѕијклљмнњопрстќуфхцчџш",
    "mn": "абвгдеёжзийклмноөпрстуүфхцчшщъыьэюя",
    "ru": "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "sr": "абвгдђежзијклљмнњопрстћуфхцчџш",
    "sr@latin": "čćđšž",
    "uk": "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя",
    # Greek, with the accented and dialytika forms ordinary prose carries.
    "el": "αβγδεζηθικλμνξοπρστυφχψωςάέήίόύώϊϋΐΰ",
}

# A script is spelled by the prefix of its characters' Unicode names, which is
# the only handle the stdlib gives us: unicodedata exposes no Script property.
SCRIPTS = {
    "arabic": ("ARABIC",),
    "armenian": ("ARMENIAN",),
    "bengali": ("BENGALI",),
    "cyrillic": ("CYRILLIC",),
    "devanagari": ("DEVANAGARI",),
    "ethiopic": ("ETHIOPIC",),
    "georgian": ("GEORGIAN",),
    "greek": ("GREEK",),
    "gujarati": ("GUJARATI",),
    "gurmukhi": ("GURMUKHI",),
    "han": ("CJK",),
    "hangul": ("HANGUL",),
    "hebrew": ("HEBREW",),
    "kana": ("HIRAGANA", "KATAKANA"),
    "kannada": ("KANNADA",),
    "khmer": ("KHMER",),
    "lao": ("LAO",),
    "latin": ("LATIN",),
    "malayalam": ("MALAYALAM",),
    "myanmar": ("MYANMAR",),
    "sinhala": ("SINHALA",),
    "tamil": ("TAMIL",),
    "telugu": ("TELUGU",),
    "thai": ("THAI",),
}

# Languages an alphabet cannot describe: Han is 97668 characters and Hangul
# 11172, and the rest are written with combining marks a letter list would
# omit, so an Arabic or Devanagari alphabet would flag every vowelled word.
SCRIPT_LANGS = {
    "am": ("ethiopic",), "ar": ("arabic",), "bn": ("bengali",),
    "fa": ("arabic",), "gu": ("gujarati",), "he": ("hebrew",),
    "hi": ("devanagari",), "hy": ("armenian",), "ja": ("kana", "han"),
    "ka": ("georgian",), "km": ("khmer",), "kn": ("kannada",),
    "ko": ("hangul", "han"), "lo": ("lao",), "ml": ("malayalam",),
    "mr": ("devanagari",), "my": ("myanmar",), "ne": ("devanagari",),
    "pa": ("gurmukhi",), "ps": ("arabic",), "sa": ("devanagari",),
    "si": ("sinhala",), "ta": ("tamil",), "te": ("telugu",),
    "th": ("thai",), "ti": ("ethiopic",), "ur": ("arabic",),
    "yi": ("hebrew",), "zh": ("han",),
    # 134 precomposed forms, and a hand-typed list that missed one would be a
    # false positive on ordinary prose. The whole script is the honest rule.
    "vi": ("latin",),
}

# CJK prose is written with its own punctuation, and a report flagging every
# full stop is a report nobody reads. U+3000 IDEOGRAPHIC SPACE is deliberately
# absent: it is invisible whitespace, which is the thing this tool exists to
# find, and a Japanese ledger that wants it can say so.
CJK_PUNCT = "、。〃〈〉《》「」『』【】〔〕〖〗〘〙〚〛〜〝〞〟・"

# Blank-rendering characters that carry a script's name, and so would ride
# into the exempt set on that script's prefix. They are letters by category
# and nothing by appearance, which makes them the whole reason this tool
# reports by exemption rather than by category - the README names the Hangul
# fillers as the example. A script rule must not hand them back.
#
# These five are the complete set: every Unicode character that renders blank,
# is category L or M, and matches one of the SCRIPTS prefixes. U+FFA0 is here
# for company only, being named HALFWIDTH and so never matched anyway.
NEVER_EXEMPT = frozenset("ᅟᅠㅤﾠ឴឵")

# Nothing beyond the ASCII floor. `--no-lang` and `lang = "none"` reach this.
NOTHING = (frozenset(), ())


def env_locale():
    """(value, variable) of the locale the environment asks for, or (None,
    None) where it names no language.

    POSIX precedence: LC_ALL beats LC_CTYPE beats LANG.

    C and POSIX name no language, and are skipped rather than read as a
    language we have no letters for. Skipped, not treated as an answer: this
    is CPython's doing as often as the caller's. The interpreter coerces the C
    locale at startup and puts LC_CTYPE=C.UTF-8 into its own environment (PEP
    538), which it does whenever the locale named is not one the system has
    generated. Stopping there would let a coerced LC_CTYPE outrank the LANG
    the caller actually set, and a minimal container would quietly scan Czech
    prose as though no language had been named at all.

    So an explicit LC_ALL=C no longer forces the ASCII-only set. --no-lang and
    lang = "none" say that, and say it without depending on which locales a
    machine happens to have installed.
    """
    for name in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = os.environ.get(name)
        if value and not names_no_language(value):
            return value, name
    return None, None


def names_no_language(value):
    """True for C and POSIX, which name a character set and no language."""
    return locale_keys(value)[-1] in ("c", "posix")


def locale_keys(locale):
    """Lookup keys for a locale, most specific first.

    cs_CZ.UTF-8 gives ("cs",); sr_RS@latin gives ("sr@latin", "sr"), since the
    modifier is what says which of Serbian's two scripts is meant.

    The modifier is taken off before the codeset, not after. glibc spells a
    locale language[_territory][.codeset][@modifier], so splitting on "." first
    would swallow "@latin" along with ".UTF-8" and quietly resolve
    sr_RS.UTF-8@latin - the form `locale -a` prints - to Serbian Cyrillic.
    """
    text, _, modifier = locale.strip().partition("@")
    code = text.split(".")[0].split("_")[0].lower()
    if modifier:
        return ("%s@%s" % (code, modifier.lower()), code)
    return (code,)


def spell_alphabet(letters):
    """Both cases, lower half first, the way a recorded `exempt` reads.

    This is the string the report tells people to edit, so it is deduplicated
    and kept free of ASCII. Turkish would otherwise record `İ` twice and an
    ASCII `I` besides, `ı`.upper() being the undotted capital's ASCII cousin,
    and Catalan would record its middle dot twice.

    `ß`.upper() is "SS", two characters and not a letter that could be exempted
    on its own, so a multi-character result is dropped.
    """
    out = []
    for ch in list(letters) + [ch.upper() for ch in letters]:
        if len(ch) == 1 and ch not in out and not is_ascii_exempt(ch):
            out.append(ch)
    return "".join(out)


def expand(letters):
    """Every case variant of `letters`, as a set.

    Case is expanded rather than taken literally so that a hand-written
    `exempt = "áčď"` also covers `Á Č Ď`. Nobody means a letter in one case
    only, and the recorded form carries both anyway.
    """
    out = set(letters)
    for ch in letters:
        for other in (ch.upper(), ch.lower()):
            if len(other) == 1:
                out.add(other)
    return out


def lang_exempt(name):
    """(characters, script prefixes) for a language code or a script name, or
    None when the tables have never heard of it."""
    for key in locale_keys(name):
        if key in ALPHABETS:
            return expand(ALPHABETS[key]), ()
        if key in SCRIPT_LANGS:
            chars, prefixes = set(), []
            for script in SCRIPT_LANGS[key]:
                prefixes.extend(SCRIPTS[script])
                if script in ("han", "kana"):
                    chars.update(CJK_PUNCT)
            return chars, tuple(prefixes)
        # A script may also be named outright, which is how the fallback and
        # `lang = "cyrillic"` in a ledger are spelled.
        if key in SCRIPTS:
            return set(), SCRIPTS[key]
    return None


def spec_exempt(letters, name):
    """((characters, prefixes), unrecognised-name) from an `exempt` string and
    a `lang` value, either of which may be empty."""
    chars, prefixes, unknown = expand(letters), (), None
    if name and names_no_language(name):
        # C and POSIX name no language wherever they are written, --lang and a
        # ledger included, and are the default rather than a complaint.
        name = ""
        if not letters:
            prefixes = SCRIPTS["latin"]
    if name and name.strip().lower() != "none":
        got = lang_exempt(name)
        if got is not None:
            chars |= got[0]
            prefixes = got[1]
        else:
            unknown = name
            # A valid code the tables have never heard of falls back to the
            # script charck exempted before it knew about languages at all.
            # Not when an alphabet was spelled out too: that was deliberate,
            # and widening it to the whole script would undo it.
            if not letters:
                prefixes = SCRIPTS["latin"]
    return (frozenset(chars), prefixes), unknown


def resolve_locale(lang, no_lang, table):
    """Resolve the exempt set: (exempt, origin, record, unrecognised).

    Command line, then the ledger, then the environment, then the Latin
    script, which is what charck exempted before there was a locale at all.

    `record` is the [locale] table to write down, and is set only when the
    answer came from the environment: a ledger that says so already needs no
    help, and a run is not reproducible while it depends on a variable.
    """
    if no_lang or (lang or "").strip().lower() == "none":
        where = "--no-lang" if no_lang else "--lang none"
        return NOTHING, where, None, None
    if lang:
        exempt, unknown = spec_exempt("", lang)
        return exempt, "--lang %s" % lang, None, unknown
    if table is not None:
        exempt, unknown = spec_exempt(table["exempt"], table["lang"])
        return exempt, str(table["path"]), None, unknown
    value, var = env_locale()
    if value:
        exempt, unknown = spec_exempt("", value)
        origin = "$%s=%s" % (var, value)
        if unknown is not None:
            # Nothing is recorded for a language we could not resolve. The
            # next run should ask the environment again rather than inherit a
            # guess this one had no basis for.
            return exempt, origin, None, unknown
        return exempt, origin, locale_record(value, var), None
    return (frozenset(), SCRIPTS["latin"]), "default (latin script)", \
        None, None


def locale_record(locale, var):
    """What to write into a [locale] table for an environment locale.

    An alphabet is recorded as the letters themselves, so correcting it is one
    edit to a visible string rather than an argument with a table compiled
    into the tool. A script cannot be spelled out at all, and keeps its name.
    """
    for key in locale_keys(locale):
        if key in ALPHABETS:
            letters = spell_alphabet(ALPHABETS[key])
            if not letters:
                return {"key": "lang", "value": "none", "from": locale,
                        "var": var, "note": "written in plain ASCII"}
            return {"key": "exempt", "value": letters, "from": locale,
                    "var": var, "note": "the letters this language uses"}
        if key in SCRIPT_LANGS or key in SCRIPTS:
            return {"key": "lang", "value": key, "from": locale, "var": var,
                    "note": "too many characters to list, so the script"}
    return None


def is_exempt(ch, exempt):
    """True for characters that are never reported: the ASCII floor, plus
    whatever the resolved locale allows."""
    if is_ascii_exempt(ch):
        return True
    chars, prefixes = exempt
    if ch in chars:
        # Spelled out by hand, and so meant. Only the script rule below is a
        # guess, and only it is second-guessed.
        return True
    if not prefixes or ch in NEVER_EXEMPT:
        return False
    # Letters, the marks that go with them, and the script's own punctuation
    # and digits. Arabic fathas and Devanagari vowel signs are Mn/Mc, and a
    # script exempted without them flags every vowelled word; the danda ends
    # every Hindi sentence and the Ethiopic full stop every Amharic one, so
    # leaving N and P out flags ordinary prose into uselessness. Membership is
    # still the character's own name, so this admits nothing for Latin, whose
    # punctuation is named for neither.
    if unicodedata.category(ch)[0] not in "LMNP":
        return False
    name = unicodedata.name(ch, "")
    # Ligatures carry their script's name but are paste artifacts, so they
    # stay reported - but only the ones that really are. Unicode draws the
    # line itself, with a compatibility decomposition: `ﬁ` has one and is the
    # PDF paste this rule was written for, and so does Armenian `և`, which a
    # normaliser would expand. `œ` and the Yiddish `װ ױ ײ` have none. They are
    # letters that happen to be named ligature, and matching on the name alone
    # flagged every Yiddish text this tool was just taught to read.
    # Fullwidth forms are named FULLWIDTH and never match here at all, which
    # is the same promise for the same reason.
    if "LIGATURE" in name and unicodedata.decomposition(ch).startswith("<"):
        return False
    return name.startswith(prefixes)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

class ConfigError(Exception):
    pass


def load_config(path, base=None):
    """Return ({char: entry}, [ignore pattern], locale). Raises ConfigError on
    anything malformed."""
    if not path.exists():
        return {}, [], None
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("%s: %s" % (path, exc))
    except OSError as exc:
        raise ConfigError("%s: %s" % (path, exc.strerror))

    chars = data.get("chars")
    if chars is None:
        chars = {}
    if not isinstance(chars, dict):
        raise ConfigError("%s: `chars` must be a table of [chars.\"U+XXXX\"] "
                          "entries, not %s" % (path, type(chars).__name__))

    patterns = load_patterns(path, data.get("files"))
    locale = load_locale(path, data.get("locale"))
    out = {}
    for key, entry in chars.items():
        if not KEY_RE.match(key):
            raise ConfigError(
                "%s: key %r is not of the form U+XXXX" % (path, key))
        cp = int(key[2:], 16)
        if cp > 0x10FFFF:
            raise ConfigError(
                "%s: [chars.%s] is beyond the Unicode range (max U+10FFFF)"
                % (path, key))
        if not isinstance(entry, dict):
            raise ConfigError("%s: [chars.%s] is not a table" % (path, key))

        action = entry.get("action", "")
        if action not in ACTIONS:
            raise ConfigError(
                "%s: [chars.%s] action = %r must be one of %s"
                % (path, key, action, ", ".join(repr(a) for a in ACTIONS)))

        to = entry.get("to", "")
        if action == "replace":
            if not isinstance(to, str):
                raise ConfigError(
                    "%s: [chars.%s] to = %r must be a quoted string"
                    % (path, key, to))
            if to == "":
                raise ConfigError(
                    '%s: [chars.%s] action = "replace" needs a non-empty '
                    '`to`; use action = "delete" to remove the character'
                    % (path, key))
            if chr(cp) in to:
                # Each run would reintroduce the character and grow `to` again,
                # so the file could never reach a fixed point.
                raise ConfigError(
                    "%s: [chars.%s] to = %s contains the character it "
                    "replaces, so repeated runs would never converge"
                    % (path, key, toml_string(to)))

        collapse = entry.get("collapse", False)
        if not isinstance(collapse, bool):
            raise ConfigError(
                "%s: [chars.%s] collapse = %r must be true or false"
                % (path, key, collapse))

        ch = chr(cp)
        # Only the ASCII floor is refused. It is never reported, so acting on
        # one would change bytes the report never showed. A character the
        # locale exempts is a different matter: writing it down here brings it
        # back into the report, so the decision can be seen before it applies.
        # The floor cannot be checked any wider than this in any case, since
        # the locale lives in the very file being loaded.
        if action in ("delete", "replace") and is_ascii_exempt(ch):
            raise ConfigError(
                "%s: [chars.%s] %s is never reported, so it cannot be %sd"
                % (path, key, name_of(ch), action))
        if ch in out:
            raise ConfigError(
                "%s: [chars.%s] duplicates an earlier entry for the same "
                "character" % (path, key))
        out[ch] = {"action": action, "to": to, "collapse": collapse}
    return out, patterns, locale


def load_patterns(path, files):
    """Validate a [files] table and return its ignore patterns, unparsed.

    Unknown keys are refused rather than ignored: unlike a [chars."U+XXXX"]
    entry, which carries name/cat/seen metadata the tool wrote itself, there is
    nothing here that a typo could plausibly be.
    """
    if files is None:
        return []
    if not isinstance(files, dict):
        raise ConfigError(
            "%s: `files` must be a table holding `ignore`, not %s"
            % (path, type(files).__name__))
    for key in files:
        if key != "ignore":
            raise ConfigError("%s: [files] has no `%s` key; the only one is "
                              "`ignore`" % (path, key))
    patterns = files.get("ignore", [])
    if not isinstance(patterns, list):
        raise ConfigError("%s: [files] ignore must be an array of patterns, "
                          "not %s" % (path, type(patterns).__name__))
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise ConfigError("%s: [files] ignore holds %r, which is not a "
                              "quoted string" % (path, pattern))
    return patterns


def load_locale(path, locale):
    """Validate a [locale] table and return {"exempt", "lang"}, or None.

    An unknown language code is not an error here: a valid code the tables
    have no data for falls back to a script rule with a note, the same as one
    read from the environment. Malformed is another matter, and refused.
    """
    if locale is None:
        return None
    if not isinstance(locale, dict):
        raise ConfigError(
            "%s: `locale` must be a table holding `exempt` and `lang`, not %s"
            % (path, type(locale).__name__))
    for key in locale:
        if key not in ("exempt", "lang"):
            raise ConfigError("%s: [locale] has no `%s` key; the keys are "
                              "`exempt` and `lang`" % (path, key))
    out = {}
    for key in ("exempt", "lang"):
        value = locale.get(key, "")
        if not isinstance(value, str):
            raise ConfigError("%s: [locale] %s = %r must be a quoted string"
                              % (path, key, value))
        out[key] = value
    if not out["exempt"] and not out["lang"]:
        # Silence here could mean "exempt nothing" or "fall through to the
        # environment", and guessing wrong changes what the report says.
        raise ConfigError(
            '%s: [locale] is empty, so it decides nothing; use lang = "none" '
            "to exempt nothing beyond ASCII, or remove the table to take the "
            "language from the environment" % path)
    return out


def toml_string(text):
    out = ['"']
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def render_entry(ch, count):
    action, note = suggest(ch)
    block = [
        '[chars."%s"]' % key_of(ch),
        "name   = %s" % toml_string(name_of(ch)),
        'cat    = "%s"' % unicodedata.category(ch),
        "seen   = %d" % count,
        'action = ""',
        'to     = ""',
    ]
    if action:
        block.append("# suggested: %s%s"
                     % (action, "  (%s)" % note if note else ""))
    elif note:
        block.append("# %s" % note)
    return "\n".join(block) + "\n"


FILES_RE = re.compile(r"^[ \t]*\[files\][ \t]*(?:#[^\n]*)?\r?$", re.M)
IGNORE_RE = re.compile(r"^[ \t]*ignore[ \t]*=[ \t]*\[", re.M)
NEXT_TABLE_RE = re.compile(r"^[ \t]*\[", re.M)


def array_end(text, start):
    """Scan the array opening at `start` and return (offset-to-insert-at,
    a-comma-is-needed, multi-line, indent), or None if it is not the plain
    array of strings we know how to extend.

    The insertion point is after the last element, or after the comma that
    follows it, so a comment trailing the array stays where its author put it.
    """
    i, n = start + 1, len(text)
    at, comma, indent = i, True, None
    while i < n:
        ch = text[i]
        if ch == "#":
            nl = text.find("\n", i)
            if nl == -1:
                return None                    # unterminated, so not our shape
            i = nl
        elif ch in "\"'":
            line = text.rfind("\n", 0, i) + 1
            if not text[line:i].strip():
                indent = text[line:i]
            if text[i:i + 3] == ch * 3:
                end = text.find(ch * 3, i + 3)
                if end == -1:
                    return None
                i = end + 3
            else:
                i += 1
                while i < n and text[i] != ch:
                    i += 2 if ch == '"' and text[i] == "\\" else 1
                if i >= n:
                    return None
                i += 1
            at, comma = i, False
        elif ch == ",":
            i += 1
            at, comma = i, True
        elif ch == "]":
            return at, comma, "\n" in text[start:i], indent
        elif ch.isspace():
            i += 1
        else:
            # A nested array, a number, a bare word: load_config would have
            # refused the file, so we are looking at something we misparsed.
            return None
    return None


def splice(text, inserts):
    """Apply (offset, string) insertions, last one first so the offsets
    hold."""
    for at, added in reversed(inserts):
        text = text[:at] + added + text[at:]
    return text


def ignore_insert(text, patterns):
    """The ledger text with `patterns` added to the [files] ignore array.

    None when the array cannot be found for certain - `files.ignore` written as
    a dotted key or an inline table, or an array in a shape this does not
    recognise. Guessing at a ledger is not on.
    """
    table = FILES_RE.search(text)
    if table is None:
        return None
    after = NEXT_TABLE_RE.search(text, table.end())
    region = text[table.end():after.start() if after else len(text)]
    key = IGNORE_RE.search(region)
    if key is None:
        # A [files] table without the key: write the whole key, right under the
        # header, where the eye expects it.
        body = "".join('  %s,\n' % toml_string(p) for p in patterns)
        return splice(text, [(table.end(), "\nignore = [\n%s]" % body)])
    span = array_end(text, table.end() + key.end() - 1)
    if span is None:
        return None
    at, comma, multi, indent = span
    if not multi:
        added = ("" if comma else ", ") + ", ".join(
            toml_string(p) for p in patterns)
        return splice(text, [(at, added)])
    inserts = [] if comma else [(at, ",")]
    nl = text.find("\n", at)
    rest = text[at:nl].strip() if nl != -1 else None
    if rest is not None and (not rest or rest.startswith("#")):
        # Whatever a comment on that line has to say, it is about the entry
        # already there. The comma goes in front of it, the new entries below.
        at = nl
    inserts.append((at, "".join(
        "\n%s%s," % ("  " if indent is None else indent, toml_string(p))
        for p in patterns)))
    return splice(text, inserts)


def open_ledger(path):
    """Open the ledger for reading and appending, creating it, and lock it.

    O_APPEND stays: it puts every write at the end of the file whatever the
    seek says, and where there is no fcntl to lock with that is the only thing
    keeping two runs from writing over each other. An insertion into the array
    cannot go through this handle at all, and does not: `append_patterns`
    hands that to `write_atomic` under this same lock.

    Binary, so that decoding is explicit and a ledger that is not UTF-8 comes
    back as an error rather than an exception from the middle of a read.
    """
    try:
        # The global ledger lives under ~/.config/charck/, which may not exist
        # on a first run.
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o666)
    except OSError as exc:
        raise ConfigError("%s: %s" % (path, exc.strerror))
    fh = os.fdopen(fd, "r+b")
    if fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def read_ledger(fh, path):
    """The whole file, decoded, read under the lock."""
    fh.seek(0)
    try:
        return fh.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError("%s: not valid UTF-8 (byte offset %d)"
                          % (path, exc.start))


def append_patterns(path, patterns):
    """Record `patterns` in the ledger's [files] ignore. Returns (recorded,
    the ones that have to be added by hand).

    Adding to a TOML array is the one thing an append cannot do, so this is the
    single place a ledger is edited rather than grown. The result is re-parsed
    and compared against the document it came from, and then written the way
    every other file here is written: to a temporary file, fsynced, and renamed
    over the original. A write that fails halfway leaves the old ledger whole,
    which matters more here than anywhere else, since the bytes at risk are the
    decisions the tool exists to remember.
    """
    with open_ledger(path) as fh:
        existing = read_ledger(fh, path)
        try:
            data = tomllib.loads(existing)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError("%s: %s" % (path, exc))
        files = data.get("files")
        have = list(files.get("ignore", [])) if isinstance(files, dict) else []
        fresh = []
        for pattern in patterns:
            if pattern not in have and pattern not in fresh:
                fresh.append(pattern)
        if not fresh:
            return [], []

        if not isinstance(files, dict):
            # No array to edit, so this stays an append and risks nothing that
            # is already in the file.
            parts = []
            if not existing:
                parts.append(HEADER)
            elif not existing.endswith("\n"):
                parts.append("\n")
            parts.append("[files]\nignore = [\n")
            parts.extend('  %s,\n' % toml_string(p) for p in fresh)
            parts.append("]\n\n")
            fh.seek(0, os.SEEK_END)
            fh.write("".join(parts).encode("utf-8"))
            return fresh, []

        new = ignore_insert(existing, fresh)
        if new is None:
            return [], fresh
        # The whole document, not just the array: an insertion that lands in
        # the wrong place can still parse, and comparing the parse against the
        # one we started from is what catches it.
        want = dict(data)
        want["files"] = dict(files, ignore=have + fresh)
        try:
            if tomllib.loads(new) != want:
                return [], fresh
        except tomllib.TOMLDecodeError:
            return [], fresh
        write_atomic(path, new)
        return fresh, []


def render_locale(record):
    return (
        "[locale]\n"
        "# from $%s=%s - %s.\n"
        "# Edit freely: what is named here is never reported.\n"
        "%s = %s\n"
        % (record["var"], record["from"], record["note"],
           record["key"], toml_string(record["value"])))


def append_locale(path, record):
    """Append a [locale] table, unless the ledger already has one.

    Append-only, like the character entries and for the same reason: a table
    that goes at the end needs no rewrite, so hand edits and comments survive.
    Re-read under the lock, so of two concurrent first runs only one writes.
    """
    with open_ledger(path) as fh:
        existing = read_ledger(fh, path)
        if LOCALE_RE.search(existing):
            return False
        parts = []
        if not existing:
            parts.append(HEADER)
        elif not existing.endswith("\n"):
            # A hand-edited file may lack a final newline; appending straight
            # on to that last line would produce invalid TOML.
            parts.append("\n")
        parts.append(render_locale(record))
        fh.seek(0, os.SEEK_END)
        fh.write("".join(parts).encode("utf-8"))
        return True


def append_entries(path, chars, counts):
    """Append undecided entries for `chars`. Never rewrites existing bytes, so
    hand edits, comments and ordering survive.

    Holds an exclusive lock and re-reads under it, so two concurrent runs
    cannot append the same table twice and brick the ledger.
    """
    with open_ledger(path) as fh:
        existing = read_ledger(fh, path)
        present = {m.group(1).upper() for m in TABLE_RE.finditer(existing)}
        fresh = [ch for ch in chars if key_of(ch) not in present]
        if not fresh:
            return []
        parts = []
        if not existing:
            parts.append(HEADER)
        elif not existing.endswith("\n"):
            # A hand-edited file may lack a final newline; appending straight
            # on to that last line would produce invalid TOML.
            parts.append("\n")
        parts.append("\n".join(render_entry(ch, counts[ch]) for ch in fresh))
        parts.append("\n")
        fh.seek(0, os.SEEK_END)
        fh.write("".join(parts).encode("utf-8"))
        return fresh


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------

def read_source(path):
    """Return (text, None) or (None, reason-it-was-skipped)."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, "unreadable (%s)" % exc.strerror

    # BOM sniff first: UTF-16/32 are full of NUL bytes and would otherwise be
    # misreported as binary.
    for bom, enc in ((b"\xff\xfe\x00\x00", "UTF-32-LE"),
                     (b"\x00\x00\xfe\xff", "UTF-32-BE"),
                     (b"\xff\xfe", "UTF-16-LE"),
                     (b"\xfe\xff", "UTF-16-BE")):
        if raw.startswith(bom):
            return None, "unsupported encoding (%s)" % enc

    # Whole file, not a prefix: a NUL past an arbitrary window would otherwise
    # let a binary through and expose it to --fix.
    if b"\x00" in raw:
        return None, "binary (NUL byte)"

    try:
        # Plain utf-8, not utf-8-sig: a leading BOM must surface as a normal
        # U+FEFF finding at line 1 col 1, not be silently swallowed.
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return None, "not valid UTF-8 (byte offset %d)" % exc.start


def iter_lines(text):
    """Yield (lineno, line-with-terminator), splitting only on \\n.

    Deliberately not str.splitlines(), which also splits on U+2028, U+2029,
    U+0085, \\x0b and \\x0c - the very characters we need to report.
    """
    start, lineno = 0, 1
    while start < len(text):
        idx = text.find("\n", start)
        if idx == -1:
            yield lineno, text[start:]
            return
        yield lineno, text[start:idx + 1]
        start, lineno = idx + 1, lineno + 1


def line_body(line):
    """Strip exactly one line terminator, CRLF or LF, and nothing more."""
    if line.endswith("\n"):
        line = line[:-1]
        if line.endswith("\r"):
            # CRLF is a line terminator, not a stray control.
            line = line[:-1]
    return line


def scan_text(text, exempt, decided=frozenset()):
    """Yield (lineno, col, char, body) for every character the locale does not
    exempt.

    `decided` holds the characters the ledger acts on, and they are reported
    whatever the locale says: a decision that could never be seen could never
    be applied either. It holds no undecided entry and no floor character, so
    adding a letter to [locale] exempt silences it even after the tool has
    asked about it, and printable ASCII stays unreportable however the ledger
    is edited. `main` builds it.
    """
    for lineno, line in iter_lines(text):
        body = line_body(line)
        for col, ch in enumerate(body, start=1):
            if ch in decided or not is_exempt(ch, exempt):
                yield lineno, col, ch, body


def safe_context(text):
    """Controls other than the marked one would move the terminal cursor and
    scramble the aligned report, so blank them out."""
    out = []
    for ch in text:
        cp = ord(ch)
        if ch == "\t" or cp < 0x20 or cp == 0x7F or 0x80 <= cp <= 0x9F:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def render(body, col, ch):
    i = col - 1
    lo = max(0, i - CONTEXT)
    hi = min(len(body), i + 1 + CONTEXT)
    return "%s%s«%s»%s%s" % (
        "…" if lo > 0 else "",
        safe_context(body[lo:i]), key_of(ch), safe_context(body[i + 1:hi]),
        "…" if hi < len(body) else "",
    )


# --------------------------------------------------------------------------
# rewriting
# --------------------------------------------------------------------------

def build_pattern(config):
    """One alternation over every decided character.

    A single left-to-right re.sub pass means replacement output is never
    re-examined, so a `to` value may safely contain a character that is itself
    configured, results do not depend on entry order, and repeated runs
    converge.
    """
    order, parts = [], []
    for ch, entry in sorted(config.items(), key=lambda kv: ord(kv[0])):
        if entry["action"] not in ("delete", "replace"):
            continue
        atom = re.escape(ch)
        if ch == "\r":
            # A CR ending a CRLF is a line terminator and is never reported, so
            # it must never be rewritten either.
            atom += r"(?!\n)"
        if entry["collapse"]:
            atom = r"[ \t]*" + atom + r"[ \t]*"
        parts.append("(?P<c%d>%s)" % (len(order), atom))
        order.append(ch)
    if not order:
        return None, []
    return re.compile("|".join(parts)), order


def apply_decisions(text, config):
    """Apply decided actions. Returns (new_text, number_of_characters_changed).
    Undecided and ignored characters pass through."""
    pattern, order = build_pattern(config)
    if pattern is None:
        return text, 0

    def repl(match):
        ch = order[int(match.lastgroup[1:])]
        entry = config[ch]
        if entry["action"] == "delete":
            return ""
        # `to` is data, never a regex replacement template: a backslash in it
        # must survive verbatim rather than being read as an escape.
        to = entry["to"]
        if not entry["collapse"]:
            return to
        got = match.group()
        start = match.start()
        if start == 0 or match.string[start - 1] == "\n":
            # At the start of a line the leading run is indentation, not
            # spacing around the character, so keep it and only collapse to
            # its right.
            indent = got[:len(got) - len(got.lstrip(" \t"))]
            return indent + to.lstrip(" \t")
        return to

    return pattern.subn(repl, text)


def write_atomic(path, text):
    # Follow symlinks: rewrite the file the content was read from, and leave
    # the link itself in place.
    target = Path(os.path.realpath(path))
    mode = target.stat().st_mode
    # mkstemp, not a fixed name: a predictable sibling could already exist
    # (and be clobbered) or be a symlink pointing somewhere else. It is
    # created 0600, so the content is never briefly world-readable.
    fd, tmpname = tempfile.mkstemp(dir=str(target.parent),
                                   prefix="." + target.name + ".",
                                   suffix=".charck-tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmpname, mode)
        os.replace(tmpname, target)
    except BaseException:
        try:
            os.unlink(tmpname)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# ignoring
# --------------------------------------------------------------------------

# Applied before anything a ledger or a .gitignore says, so `!.github/` can
# bring one of them back.
DEFAULT_IGNORE = (".*", "node_modules/", "__pycache__/", ".venv/", "venv/",
                  "target/", "dist/")

# Never walked into, whatever the patterns say and whatever --no-ignore says. A
# repository is full of text files that are not prose - config, HEAD, loose
# refs, COMMIT_EDITMSG - and a --fix in there would corrupt the repository.
ALWAYS_PRUNE = {".git"}


# Bracket expressions such as [[:alpha:]] are part of the syntax, and Python's
# re has no equivalent, so they are spelled out.
POSIX_CLASS = {
    "alnum": "0-9A-Za-z", "alpha": "A-Za-z", "blank": r" \t",
    "cntrl": r"\x00-\x1f\x7f", "digit": "0-9", "graph": r"\x21-\x7e",
    "lower": "a-z", "print": r"\x20-\x7e", "punct": r"!-/:-@\[-`{-~",
    "space": r" \t\n\r\f\v", "upper": "A-Z", "xdigit": "0-9A-Fa-f",
}


def class_regex(body):
    """Translate the body of a bracket expression.

    Every literal goes through `re.escape`, because the two syntaxes disagree
    about what a backslash means inside a class: gitignore reads `[\\w]` as the
    single letter `w`, Python as a whole character class. Copying the body
    verbatim would silently drop files from the scan.
    """
    negated = body[:1] in ("!", "^")
    out, i, n = [], 1 if negated else 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\\" and i + 1 < n:
            out.append(re.escape(body[i + 1]))
            i += 2
        elif body[i:i + 2] == "[:":
            end = body.find(":]", i + 2)
            if end == -1:
                out.append(re.escape("["))
                i += 1
                continue
            name = body[i + 2:end]
            if name not in POSIX_CLASS:
                raise ValueError("[:%s:] is not a character class" % name)
            out.append(POSIX_CLASS[name])
            i = end + 2
        elif ch == "/":
            # No wildcard in this syntax may match a separator, so a `/` in a
            # class is dropped rather than left to match one.
            i += 1
        elif ch == "-" and out and i + 1 < n:
            out.append("-")                    # a range, kept bare
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    inner = "".join(out)
    if not inner:
        return "[^/]" if negated else "(?!)"   # (?!) can never match
    return "[^/%s]" % inner if negated else "[%s]" % inner


def pattern_regex(text):
    """Translate a gitignore pattern body into a regex source.

    `*` and `?` never cross a `/`. `**` does, but only where it is a whole path
    segment: git reads the one in `d1/**b.md` as a plain `*`. Negation, the
    directory-only trailing slash and anchoring are handled in make_rule.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            # A backslash takes whatever follows it literally.
            out.append(re.escape(text[i + 1]))
            i += 2
        elif ch == "*":
            j = i
            while j < n and text[j] == "*":
                j += 1
            segment = ((i == 0 or text[i - 1] == "/")
                       and (j == n or text[j] == "/"))
            if j - i >= 2 and segment:
                if j == n:
                    # Trailing **: everything below. (?s:), since a newline is
                    # a legal character in a path segment and this tool of all
                    # tools does not get to pretend otherwise.
                    out.append("(?s:.*)")
                else:
                    out.append("(?:[^/]+/)*")
                    j += 1
                    # Consecutive globstars collapse. **/**/x means **/x, and
                    # stacking the same quantified group is what turns a
                    # degenerate pattern into minutes of backtracking.
                    while text[j:j + 3] == "**/":
                        j += 3
            else:
                out.append("[^/]*")
            i = j
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            j = i + 1
            if j < n and text[j] in "!^":
                j += 1
            if j < n and text[j] == "]":       # a ] first in a class is data
                j += 1
            while j < n and text[j] != "]":
                if text[j] == "\\":
                    j += 2
                elif text[j:j + 2] == "[:":
                    end = text.find(":]", j + 2)
                    j = end + 2 if end != -1 else j + 2
                else:
                    j += 1
            if j >= n:                         # unterminated, so not a class
                out.append(re.escape(ch))
                i += 1
            else:
                out.append(class_regex(text[i + 1:j]))
                i = j + 1
        else:
            out.append("/" if ch == "/" else re.escape(ch))
            i += 1
    return "".join(out)


def make_rule(pattern, base, where, label, absolute=False):
    """Compile one pattern. Raises ConfigError on anything unusable.

    With `absolute`, an anchored pattern carries `base` in its own expression
    rather than relying on the group's prefix. That is what lets a --exclude
    apply to a tree outside the working directory: the unanchored patterns
    match wherever the walk goes, and the anchored ones still mean "under
    here".
    """
    text, negated = pattern, False
    if text.startswith("!"):
        text, negated = text[1:], True
    elif text.startswith("\\!"):
        text = text[1:]                        # an escaped, literal leading !
    dir_only = text.endswith("/")
    if dir_only:
        text = text[:-1]

    # Anchored means "relative to the file this pattern came from", exactly as
    # gitignore anchors. A leading **/ is the explicit any-depth form, and the
    # slashes after it must not re-anchor it.
    anchored = False
    if text.startswith("/"):
        anchored, text = True, text[1:]
    elif text.startswith("**/"):
        while text.startswith("**/"):          # **/**/x is **/x
            text = text[3:]
    elif "/" in text:
        anchored = True

    fault = None
    if not text:
        fault = "is empty"
    elif (len(text) - len(text.rstrip("\\"))) % 2:
        fault = "ends in a lone backslash"
    else:
        segments = text.split("/")
        if "" in segments:
            fault = "has an empty path segment"
        elif "." in segments or ".." in segments:
            fault = "has a . or .. segment, and patterns are relative to " \
                    "the directory of the file they are written in"
    if fault is None and anchored and base is None:
        fault = ("is anchored, and the global ledger has no directory to "
                 "anchor it to; a pattern with a slash belongs in a project %s"
                 % LOCAL_NAME)
    if fault is not None:
        raise ConfigError("%s: ignore pattern %s %s"
                          % (where, toml_string(pattern), fault))

    if not anchored:
        prefix = "(?s:.*/)?"
    elif absolute:
        prefix = re.escape(base.as_posix().rstrip("/") + "/")
    else:
        prefix = ""
    try:
        compiled = re.compile(prefix + pattern_regex(text))
    except (re.error, ValueError) as exc:
        raise ConfigError("%s: ignore pattern %s is not usable (%s)"
                          % (where, toml_string(pattern), exc))
    return {"re": compiled, "neg": negated, "dir": dir_only, "anch": anchored,
            "pat": pattern, "src": label, "file": str(where)}


def rule_group(patterns, base, where, label, bad=None, absolute=False):
    """Return (prefix, rules): `prefix` is what a path must start with for the
    group to apply at all, and None means the rules match at any depth.

    With `bad` given an unusable pattern is collected there instead of raising.
    A .gitignore is not ours to validate, and one odd line in someone else's
    file must not stop the run; a pattern in our own ledger still does.
    """
    rules = []
    for pattern in patterns:
        try:
            rules.append(make_rule(pattern, base, where, label, absolute))
        except ConfigError:
            if bad is None:
                raise
            bad.append((pattern, where))
    if base is None or absolute:
        return None, rules
    return base.as_posix().rstrip("/") + "/", rules


def record_form(rule, rel):
    """A --exclude pattern as it has to be written down in the ledger.

    Every pattern anchors to the directory of the file holding it, and the
    ledger is not always the directory you are standing in. An anchored one is
    therefore rewritten to go on meaning the place it meant during the run:
    from /proj/sub, with the ledger at /proj, `/build/` is recorded as
    `/sub/build/`. An unanchored pattern says "at any depth" in either file,
    so it goes down exactly as it was typed.
    """
    if not rule["anch"] or rel is None or rel == Path("."):
        return rule["pat"]
    text = rule["pat"]
    bang = ""
    if text.startswith("!"):
        bang, text = "!", text[1:]
    # A directory named `*` or `[x]` is a thing that exists, and pasting one
    # into a pattern unescaped would quietly widen what it matches.
    lead = re.sub(r"([\\*?\[\]])", r"\\\1", rel.as_posix())
    return "%s/%s/%s" % (bang, lead, text.lstrip("/"))


def ignored_by(groups, path, is_dir):
    """The last rule matching `path`, or None.

    Later groups and later patterns win, which is what makes `!` a re-include
    and what makes a deeper .gitignore override a shallower one.
    """
    text = path.as_posix()
    hit = None
    for prefix, rules in groups:
        if prefix is None:
            # No directory to be relative to, so the pattern is matched against
            # the whole path; every such rule carries a (?:.*/)? prefix.
            rel = text
        elif text.startswith(prefix):
            rel = text[len(prefix):]
        else:
            continue
        for rule in rules:
            if rule["dir"] and not is_dir:
                continue
            if rule["re"].fullmatch(rel):
                hit = rule
    return hit


def read_ignore_file(path, bad=None):
    """Pattern lines of a .gitignore, comments and blank lines dropped.

    Trailing spaces go, as git drops them, unless escaped. Ledger patterns are
    TOML strings written deliberately and are never stripped.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Silence here would mean scanning files that should have been left
        # out, with nothing at all to say why.
        if bad is not None:
            bad.append((None, path))
        return []
    # A BOM would otherwise become part of the first pattern, which would then
    # match nothing. An invisible character defeating the tool that hunts them
    # is not an irony we are going to ship.
    if text.startswith("\ufeff"):
        text = text[1:]
    out = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        kept = line.rstrip(" ")
        if kept != line and (len(kept) - len(kept.rstrip("\\"))) % 2:
            # An odd number of backslashes before the run escapes exactly one
            # space, so that one stays and the rest go.
            kept = line[:len(kept) + 1]
        if kept:
            out.append(kept)
    return out


def inside_git(path):
    """True when `path` is a .git or lies within one, symlinks resolved.

    Pruning by name alone is not enough. A walk reaches the same files by being
    pointed straight at .git, and a symlink in an ordinary tree can lead back
    into one - `write_atomic` follows it to the real file, so a --fix would
    rewrite a loose ref or a config while the report named the link.
    """
    try:
        real = Path(os.path.realpath(str(path)))
    except OSError:
        return False
    return any(part in ALWAYS_PRUNE for part in real.parts)


def git_root(start):
    """Nearest directory at or above `start` holding a .git, or None. A .git
    file rather than a directory is what a worktree or a submodule has."""
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return None


def git_groups(top, root, bad):
    """Ignore groups from the .gitignore files between `root` and `top`,
    shallowest first. The one in `top` itself is picked up by the walk, along
    with every one below it."""
    groups = []
    # .git/info/exclude ranks below every .gitignore, so it is read first.
    exclude = root / ".git" / "info" / "exclude"
    if exclude.is_file():
        groups.append(rule_group(read_ignore_file(exclude, bad), root, exclude,
                                 "gitignore", bad))
    chain = []
    if top != root:
        directory = top.parent
        while True:
            chain.append(directory)
            if directory == root or directory == directory.parent:
                break
            directory = directory.parent
    for directory in reversed(chain):
        path = directory / ".gitignore"
        if path.is_file():
            groups.append(rule_group(read_ignore_file(path, bad), directory,
                                     path, "gitignore", bad))
    return groups


# --------------------------------------------------------------------------
# walking
# --------------------------------------------------------------------------

def walk_tree(top, exts, ign, ignored):
    """Yield the files under `top` that the ignore rules leave alone, adding
    what they excluded to `ignored` as (path, rule).

    A directory that is ignored is never descended into, so a pattern cannot
    re-include a file underneath one - the same limit gitignore has, and the
    reason `!` has to name the directory.
    """
    try:
        base = top.resolve()
    except OSError:
        base = Path(os.path.abspath(str(top)))
    # Outside a work tree git applies no ignore rules at all, so neither do
    # we; a stray .gitignore in a tarball is not policy anyone recorded. A
    # walk that starts outside one can still descend into a repository of its
    # own.
    repo = git_root(base) if ign["git"] else None
    # os.walk builds each root by joining, so a resolved absolute path can be
    # carried down the same way. Resolving per file would be slower, and
    # resolving nothing would break anchoring for a relative PATH argument.
    absolute = {str(top): base}
    inherited = {str(top): git_groups(base, repo, ign["bad"]) if repo else []}
    tracked = {str(top): repo is not None}

    for root, dirs, files in os.walk(str(top)):
        here = absolute[root]
        groups = inherited[root]
        inside = tracked[root]
        if inside:
            local = here / ".gitignore"
            if local.is_file():
                groups = groups + [
                    rule_group(read_ignore_file(local, ign["bad"]), here,
                               local, "gitignore", ign["bad"])]
                inherited[root] = groups
        active = ign["head"] + groups + ign["tail"]

        keep = []
        for name in sorted(dirs):
            if name in ALWAYS_PRUNE:
                continue
            rule = ignored_by(active, here / name, True)
            if rule is not None and not rule["neg"]:
                ignored.append((Path(root) / name, rule))
                continue
            key = os.path.join(root, name)
            absolute[key] = here / name
            inherited[key] = groups
            tracked[key] = inside or (ign["git"]
                                      and (here / name / ".git").exists())
            keep.append(name)
        dirs[:] = keep

        for name in sorted(files):
            if name in ALWAYS_PRUNE:
                # A worktree or a submodule has .git as a file, not a
                # directory, and --fix must not reach that one either.
                continue
            if exts and Path(name).suffix not in exts:
                continue
            rule = ignored_by(active, here / name, False)
            if rule is not None and not rule["neg"]:
                ignored.append((Path(root) / name, rule))
                continue
            child = Path(root) / name
            if child.is_symlink() and inside_git(child):
                continue
            # is_file() is False for FIFOs, sockets and device nodes; reading a
            # FIFO would block forever.
            if child.is_file():
                yield child


def collect(paths, exts, ign):
    """Return ([(path, was-named-directly)], [(path, rule)]), deduplicated and
    in stable order. The second list is what the ignore rules excluded.

    A path named directly is never matched against a pattern. Naming a file is
    an explicit request, and it is the same escape hatch that reaches a SELF
    file; the directory you name is exempt too, everything under it is not.
    """
    found, index, ignored = [], {}, []

    def add(path, direct):
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in index:
            # Named directly anywhere on the command line wins, so the SELF
            # escape hatch does not depend on argument order.
            if direct:
                pos = index[resolved]
                found[pos] = (found[pos][0], True)
            return
        index[resolved] = len(found)
        found.append((path, direct))

    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            if inside_git(path):
                # Naming it is the one way past the prune inside the walk, and
                # a --fix over loose refs would corrupt the repository. Refuse
                # loudly: a silent empty walk would read as a clean tree. A
                # single file inside .git named directly is still scanned.
                print("error: .git is never walked: %s" % raw, file=sys.stderr)
                raise SystemExit(2)
            for child in walk_tree(path, exts, ign, ignored):
                add(child, False)
        elif path.is_file():
            add(path, True)
        elif path.exists():
            print("error: not a regular file: %s" % raw, file=sys.stderr)
            raise SystemExit(2)
        else:
            print("error: no such file or directory: %s" % raw,
                  file=sys.stderr)
            raise SystemExit(2)

    # Two PATH arguments may overlap, and a path counted twice would inflate
    # the ignored total the same way a file scanned twice would inflate the
    # findings. abspath, not resolve: no syscall, and this is only bookkeeping.
    once, seen = [], set()
    for path, rule in ignored:
        key = os.path.abspath(str(path))
        if key not in seen:
            seen.add(key)
            once.append((path, rule))
    return found, once


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def describe_exempt(exempt):
    """The exempt set on one line: the letters where they were spelled out,
    the script names where they could not be."""
    chars, prefixes = exempt
    parts = []
    if chars:
        parts.append("".join(sorted(chars, key=ord)))
    if prefixes:
        parts.extend(sorted(name for name, spelling in SCRIPTS.items()
                            if set(spelling) <= set(prefixes)))
    return " + ".join(parts) if parts else "nothing beyond ASCII"


def describe(entry):
    if entry is None or entry["action"] == "":
        return "UNDECIDED"
    if entry["action"] == "delete":
        return "delete"
    if entry["action"] == "ignore":
        return "ignore"
    return "replace %s" % toml_string(entry["to"])


def build_parser():
    ap = argparse.ArgumentParser(
        prog=SCRIPT.stem,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", metavar="PATH",
                    help="files or directories (directories are walked)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; this is the default")
    ap.add_argument("--fix", action="store_true",
                    help="apply decided actions, rewriting files in place")
    ap.add_argument("--no-append", action="store_true",
                    help="report only; do not add new entries to the config")
    ap.add_argument("--config", metavar="PATH", default=None,
                    help="use only this ledger, ignoring both normal layers "
                         "(default: %s overridden per character by the "
                         "nearest %s at or above the working directory)"
                         % (global_config(), LOCAL_NAME))
    ap.add_argument("--lang", metavar="LOCALE", default=None,
                    help="exempt the letters this language writes with, "
                         "instead of asking the environment; a locale, a "
                         "language code, a script name, or \"none\"")
    ap.add_argument("--no-lang", action="store_true",
                    help="exempt nothing beyond printable ASCII, whatever "
                         "the ledger and the environment say")
    ap.add_argument("--ext", metavar="LIST", default="",
                    help="restrict a directory walk by extension, "
                         "e.g. .md,.toml")
    ap.add_argument("--exclude", metavar="PATTERN", action="append",
                    default=[],
                    help="leave this out of a walk, in gitignore syntax; "
                         "repeatable, outranks every recorded pattern, and is "
                         "written to the ledger unless --no-append")
    ap.add_argument("--no-ignore", action="store_true",
                    help="apply no ignore patterns at all, built-in ones "
                         "included; .git is still never walked")
    ap.add_argument("--no-gitignore", action="store_true",
                    help="do not read .gitignore files; the ledger's own "
                         "[files] ignore patterns still apply")
    ap.add_argument("--list", action="store_true",
                    help="print the config as a decision table and exit")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also show occurrences of ignored characters, and "
                         "every file skipped or ignored, with the reason")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="summary only, no per-occurrence detail")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)

    layers, config_path = resolve_layers(args.config)
    config, source, groups, locale_table = merge_layers(layers)
    exempt, origin, locale_new, unknown_lang = resolve_locale(
        args.lang, args.no_lang, locale_table)
    # A character the ledger acts on is reported whatever the locale says, so
    # that --fix never changes bytes the report did not show.
    #
    # Only `delete` and `replace`, and this is load-bearing rather than
    # tidiness. Undecided entries are written by the tool itself, for every
    # character it has ever seen, so an entry carries no intent until an
    # action is filled in. Letting an undecided one un-exempt would mean a
    # letter, once asked about, could never be silenced by adding it to
    # [locale] exempt - which is the correction the report tells you to make.
    # Never a floor character either, though load_config already refuses to
    # act on one.
    decided = frozenset(ch for ch, entry in config.items()
                        if entry["action"] in ("delete", "replace")
                        and not is_ascii_exempt(ch))

    defaults = rule_group(DEFAULT_IGNORE, None, "built-in defaults", "default")
    # A --exclude anchors to the working directory, and goes last so it
    # outranks every recorded pattern. --no-ignore turns off what was
    # recorded; a pattern given on the same command line is what is being
    # asked for right now, so it survives.
    cli = [rule_group(args.exclude, Path.cwd(), "--exclude", "cli",
                      absolute=True)] if args.exclude else []
    ign = {
        "head": [] if args.no_ignore else [defaults],
        "tail": ([] if args.no_ignore else groups) + cli,
        "git": not (args.no_ignore or args.no_gitignore),
        "bad": [],                 # unusable .gitignore lines, shown under -v
    }

    if args.list:
        for path, base in layers:
            print("# %s" % path)
        print("exempt: %s   [%s]" % (describe_exempt(exempt), origin))
        if unknown_lang:
            print("unknown language %s, falling back"
                  % toml_string(unknown_lang))
        print()
        if config:
            print("%-9s %-42s %-16s %s" % ("CODE", "NAME", "DECISION", "FROM"))
            for ch in sorted(config, key=ord):
                layer = ("local" if source[ch].name == LOCAL_NAME
                         else "global")
                print("%-9s %-42s %-16s %s"
                      % (key_of(ch), name_of(ch)[:42], describe(config[ch]),
                         layer))
        else:
            print("no decisions recorded in %s" % config_path)
        print()
        print("%-9s %s" % ("FROM", "IGNORE"))
        for _, rules in ign["head"] + ign["tail"]:
            for rule in rules:
                print("%-9s %s" % (rule["src"], rule["pat"]))
        # Which .gitignore files apply depends on what is being walked, and
        # --list is given no path, so they cannot be listed here.
        print("later patterns win; .gitignore is read during a walk and is "
              "not listed here")
        return 0

    if args.lang is not None and not args.lang.strip():
        # Every other unusable value says so; the empty string would otherwise
        # be the one that silently behaves as though the flag were absent.
        ap.error("--lang needs a language; --no-lang exempts nothing")
    if not args.paths:
        ap.error("no PATH given")
    if args.fix and args.dry_run:
        ap.error("--dry-run and --fix are mutually exclusive")

    # What each --exclude will be written down as, worked out before the walk:
    # a pattern that cannot be recorded has to say so at once rather than after
    # a scan of the tree.
    to_record = []
    if args.exclude and not args.no_append:
        # The directory a recorded pattern will anchor to, which has to be the
        # one merge_layers hands that same ledger: resolved for an explicit
        # --config, and the directory the file sits in for a discovered one,
        # which are not the same thing when the ledger is a symlink.
        ledger_dir = (Path(args.config).resolve().parent
                      if args.config is not None else config_path.parent)
        cwd = Path.cwd()
        try:
            rel = cwd.relative_to(ledger_dir)
        except ValueError:
            rel = None
        for rule in cli[0][1]:
            if rule["anch"] and rel is None:
                raise ConfigError(
                    "--exclude %s is anchored to %s, which is not under the "
                    "ledger it would be recorded in (%s); use --no-append, or "
                    "a pattern without a slash"
                    % (toml_string(rule["pat"]), cwd, config_path))
            to_record.append(record_form(rule, rel))
        # A ledger's patterns only apply under its own directory, so a pattern
        # recorded for a tree somewhere else would do nothing on the next run
        # while doing its job on this one. Refuse rather than hand back a
        # pattern that quietly means less tomorrow than it did today.
        for raw in args.paths:
            root = Path(raw)
            try:
                outside = root.is_dir() and not root.resolve().is_relative_to(
                    ledger_dir)
            except OSError:
                outside = False
            if outside:
                raise ConfigError(
                    "--exclude would be recorded in %s, which is not above "
                    "%s, so the pattern could never apply to that tree "
                    "again; use --no-append" % (config_path, raw))

    exts = tuple(e if e.startswith(".") else "." + e
                 for e in (x.strip() for x in args.ext.split(",")) if e)
    targets, ignored = collect(args.paths, exts, ign)
    # Every ledger in play is self-protected, not just the one being appended
    # to: rewriting a ledger's own `to` values would destroy the decisions the
    # run is acting on.
    config_resolved = set()
    for path in [*(p for p, _ in layers), config_path]:
        try:
            config_resolved.add(path.resolve())
        except OSError:
            pass

    findings = []          # (path, lineno, col, ch, body, is_self)
    counts = {}            # ch -> total occurrences
    cache = {}             # path -> text, for files with findings
    scanned = skipped = 0
    skips = []

    for path, direct in targets:
        text, reason = read_source(path)
        if text is None:
            skipped += 1
            skips.append((path, reason))
            continue
        scanned += 1
        try:
            is_self = not direct and (path.name in SELF_FILES
                                      or path.resolve() in config_resolved)
        except OSError:
            is_self = not direct and path.name in SELF_FILES
        got = list(scan_text(text, exempt, decided))
        if got:
            cache[path] = text
        for lineno, col, ch, body in got:
            findings.append((path, lineno, col, ch, body, is_self))
            counts[ch] = counts.get(ch, 0) + 1

    # Characters with no entry yet get appended as undecided, always to a local
    # ledger; the global one is never written to.
    unknown = [ch for ch in sorted(counts, key=ord) if ch not in config]
    appended, recorded, unrecorded = [], [], []
    created_ledger = locale_written = False
    # The locale rides along with a write that was happening anyway, and never
    # causes one. A clean tree wrote nothing before this feature and must
    # write nothing now: creating a ledger for a run with no findings is
    # presumptuous, and on a read-only checkout it would turn a green build
    # gate into exit 2.
    if (unknown or to_record) and not args.no_append:
        created_ledger = not config_path.exists()
        if locale_new:
            # First, so a ledger this run creates opens with the language it
            # was read under rather than burying it under the findings.
            locale_written = append_locale(config_path, locale_new)
        if to_record:
            # Before the characters, so a ledger this run creates opens with
            # the patterns instead of burying them under the first character
            # table.
            recorded, unrecorded = append_patterns(config_path, to_record)
        if unknown:
            appended = append_entries(config_path, unknown, counts)
            for ch in unknown:
                config[ch] = {"action": "", "to": "", "collapse": False}

    def visible(ch):
        entry = config.get(ch)
        return not (entry and entry["action"] == "ignore" and not args.verbose)

    shown = [f for f in findings if visible(f[3])]

    if not args.quiet and shown:
        current = None
        for path, lineno, col, ch, body, is_self in shown:
            if path != current:
                print("%s%s" % (path, "   [SELF - not rewritten]"
                                if is_self else ""))
                current = path
            print("  line %4d, col %3d   %-8s %-30s %-14s %s"
                  % (lineno, col, key_of(ch), name_of(ch)[:30],
                     describe(config.get(ch)), render(body, col, ch)))
        print()

    # ---- apply -----------------------------------------------------------
    changed_files = changed_chars = 0
    failures = []
    actionable = {ch: e for ch, e in config.items()
                  if e["action"] in ("delete", "replace")}
    blocked = sorted({f[3] for f in findings if f[5] and f[3] in actionable},
                     key=ord)

    if args.fix:
        dirty = []
        for path, lineno, col, ch, body, is_self in findings:
            if ch in actionable and not is_self and path not in dirty:
                dirty.append(path)
        for path in dirty:
            # Reuse the scanned text rather than re-reading: the file cannot
            # have changed underneath us between report and rewrite.
            new, n = apply_decisions(cache[path], actionable)
            if new == cache[path]:
                continue
            try:
                write_atomic(path, new)
            except OSError as exc:
                failures.append((path, exc.strerror or str(exc)))
                continue
            changed_files += 1
            changed_chars += n

    # ---- summary ---------------------------------------------------------
    if counts:
        print("Summary")
        for ch in sorted(counts, key=lambda c: (-counts[c], ord(c))):
            entry = config.get(ch)
            if entry and entry["action"] == "ignore" and not args.verbose:
                continue
            if ch not in unknown:
                flag = ""
            elif ch in appended:
                flag = "   <- appended to config"
            else:
                flag = "   <- not in config"
            print("  %-8s %-32s %4d   %s%s"
                  % (key_of(ch), name_of(ch)[:32], counts[ch],
                     describe(entry), flag))
        print()

    if args.verbose:
        # Every "this file was not scanned" line lives here. A run over a
        # content tree finds images and other binaries by the hundred, and one
        # line each buries the findings the report exists for. The counts in
        # the summary are what says they happened at all.
        for path, reason in skips:
            print("  skipped %s: %s" % (path, reason))
        # The "why is this file not being scanned" question, answered the way
        # `git check-ignore -v` answers it: with the pattern and its file.
        for path, rule in ignored:
            print("  ignored %s   [%s from %s]"
                  % (path, rule["pat"], rule["file"]))
        # dict.fromkeys: one .gitignore read under two PATH arguments must not
        # report its bad line twice.
        for pattern, where in dict.fromkeys(ign["bad"]):
            if pattern is None:
                print("  unreadable %s, its patterns were not applied" % where)
            else:
                print("  unusable pattern %s in %s"
                      % (toml_string(pattern), where))

    undecided = sorted({f[3] for f in findings
                        if config.get(f[3], {}).get("action", "") == ""},
                       key=ord)

    if args.fix and changed_files:
        print("  %s fixed in %s"
              % (plural(changed_chars, "character"),
                 plural(changed_files, "file")))
    for path, reason in failures:
        print("  ERROR could not write %s: %s" % (path, reason),
              file=sys.stderr)
    if blocked:
        # Printed even under -q: silently declining to apply a recorded
        # decision is exactly the thing a caller must not miss.
        print("  %s left in self-referencing files (%s); name the file "
              "directly to rewrite it"
              % (plural(len(blocked), "decided character"),
                 ", ".join(key_of(c) for c in blocked)))
    if created_ledger:
        print("  created %s" % config_path)
    if unknown_lang:
        # Always shown, not just under -v: it says why the report looks the
        # way it does, and the fallback is wider than the language asked for.
        print("  no letters recorded for language %s; exempting %s instead"
              % (toml_string(unknown_lang), describe_exempt(exempt)))
    if locale_written:
        print("  recorded [locale] %s = %s in %s"
              % (locale_new["key"], toml_string(locale_new["value"]),
                 config_path))
    if recorded:
        print("  recorded %s in %s"
              % (plural(len(recorded), "ignore pattern"), config_path))
        shown = set()
        for typed, form in zip(args.exclude, to_record):
            # The rewrite an anchored pattern goes through is worth seeing,
            # and worth seeing once however many times it was typed.
            if form != typed and form in recorded and form not in shown:
                shown.add(form)
                print("    %s recorded as %s"
                      % (toml_string(typed), toml_string(form)))
    if unrecorded:
        # Printed even under -q: a pattern that was applied but not written
        # down is a difference between this run and the next one.
        print("  could not record %s in %s; add to [files] ignore by hand:"
              % (plural(len(unrecorded), "ignore pattern"), config_path))
        for pattern in unrecorded:
            print("    %s," % toml_string(pattern))
    if undecided:
        n = len(undecided)
        print("  %d character%s need%s a decision - %s %s"
              % (n, "" if n == 1 else "s", "s" if n == 1 else "",
                 "re-run without --no-append to record them in"
                 if args.no_append else "edit", config_path))
        # An alphabet is corrected by editing one string, not by deciding a
        # foreign name's letters one table at a time. Only when the locale
        # spelled its letters out: for a script there is nothing to extend.
        letters = [ch for ch in undecided
                   if unicodedata.category(ch)[0] == "L"]
        if letters and exempt[0] and not exempt[1]:
            one = len(letters) == 1
            print("    %s %s; if this language uses %s, add %s to [locale] "
                  "exempt rather than deciding %s"
                  % (", ".join(key_of(c) for c in letters),
                     "is a letter" if one else "are letters",
                     "it" if one else "them",
                     toml_string("".join(letters)),
                     "it" if one else "each one"))

    nfiles = len({f[0] for f in findings})
    # An ignored directory counts once and its contents are never enumerated,
    # which is the point of pruning rather than filtering.
    print("  %s in %s  (%d scanned, %d skipped, %d ignored)"
          % (plural(len(findings), "occurrence"), plural(nfiles, "file"),
             scanned, skipped, len(ignored)))

    if failures:
        return 2
    # A pattern that was applied but not written down is left to act on, the
    # same as a decided character that could not be rewritten. Not a 2: nothing
    # failed operationally, we declined to guess at a ledger.
    if args.fix:
        return 1 if (undecided or blocked or unrecorded) else 0
    outstanding = [f for f in findings
                   if config.get(f[3], {}).get("action", "") != "ignore"]
    if outstanding and not undecided and not args.quiet:
        print("  all decided; run --fix to apply")
    return 1 if (outstanding or unrecorded) else 0


def cli(argv=None):
    """Console entry point: turns exceptions into the documented exit codes.

    Both `python3 charck.py` and the installed `charck` command go through
    here, so an installed copy reports a bad ledger as exit 2 rather than a
    traceback.
    """
    try:
        return main(argv)
    except ConfigError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        # A disk that filled up, a ledger that went read-only mid-run. The
        # report already handles the ones it can carry on from; this is for the
        # rest, which used to reach the terminal as a traceback and exit 1.
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(cli())
