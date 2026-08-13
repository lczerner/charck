#!/usr/bin/env python3
"""charck - check text files for non-ASCII characters, and repair them.

Reports every character that is not printable ASCII, with codepoint, name, count
and exact position. The only exemption is Latin letters with diacritics (a e i c
r s z ...), so ordinary Latin-script prose in any language is not flagged on
every run; ligatures and fullwidth forms are still reported, being paste
artifacts rather than letters.

What to do about each character is recorded in a ledger. Detection appends
newly-seen characters there as undecided; you fill in delete / replace / ignore;
later runs apply those decisions.

There are two ledgers, and they layer per character:

  ~/.config/charck/charck.toml   your decisions, applied everywhere
  ./.charck.toml                 this project's exceptions, found by walking up
                                 from the working directory

A character decided in the local ledger uses that decision; every other
character falls back to the global one. So a project can disagree about a single
character without restating the rest.

The global ledger is never written to. It is yours to edit by hand, and nothing
the tool discovers is added to it automatically. Newly-seen characters go to the
local ledger, which is created in the working directory if none is in scope.
`--config PATH` ignores both layers and reads and appends to exactly that file.

What a walk leaves alone is recorded in the same ledger, in gitignore syntax:

  [files]
  ignore = ["build/", "*.min.js", "!keep.md"]

Those patterns are applied after a set of built-in ones (dot-files, node_modules
and the usual build output) which a `!` pattern can override, and after any
.gitignore in scope, which is read unless --no-gitignore says otherwise. A path
named directly on the command line is never ignored, and .git is never walked
into whatever the patterns say.

Caveats worth knowing:

  Cn (unassigned) is Unicode-version-dependent. A character assigned in a Unicode
  release newer than this Python's will read as unassigned here. Such findings are
  labelled so a false positive is visible before you decide to delete them.

  Co (private use, U+E000-F8FF) is where Nerd Font and Powerline glyphs live. A
  "delete" decision on those would strip prompt icons from a shell config.

  --fix replaces files atomically, which breaks hardlinks: a rewritten file gets a
  new inode, so any other name that shared the old one keeps the old content.
  Extended attributes and ACLs are not carried over either. Symlinks are followed,
  so the real file is rewritten and the link is left intact.
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

# Two layers. The global ledger holds decisions that hold everywhere; a project
# may override individual characters, and only those, in a local ledger found by
# walking up from the working directory. Deliberately not "beside the script":
# an installed copy lives in site-packages, which may be read-only and is
# replaced on upgrade, which would silently discard every recorded decision.
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

    `base` is the directory an anchored ignore pattern in that layer is relative
    to, as gitignore anchors to the directory of the file it is written in. The
    global ledger has no such directory, and None says so.

    An explicit --config replaces the whole stack, so scripted and test runs get
    exactly the file they name and nothing else.
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

    Returns (config, source, groups): source[ch] is the layer that decided it,
    and groups are the layers' ignore rules in the same order, so a local `!`
    pattern has the last word just as a local decision does.
    """
    config, source, groups = {}, {}, []
    for path, base in layers:
        chars, patterns = load_config(path, base)
        for ch, entry in chars.items():
            config[ch] = entry
            source[ch] = path
        if patterns:
            label = "global" if base is None else (
                "local" if path.name == LOCAL_NAME else "config")
            groups.append(rule_group(patterns, base, path, label))
    return config, source, groups

# This script and the ledger it is reading describe the characters we hunt, so
# rewriting them would corrupt the tool itself. Scanned and reported, but never
# rewritten unless named directly on the command line. The config file actually
# in use is added to this set at runtime.
SELF_FILES = {SCRIPT.name}

ACTIONS = ("", "delete", "replace", "ignore")
KEY_RE = re.compile(r"^U\+[0-9A-F]{4,6}$")
TABLE_RE = re.compile(r'^\[chars\."(U\+[0-9A-Fa-f]{4,6})"\]', re.M)
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
# character, so "a - b" does not become "a  -  b". Indentation at the start of a
# line is never eaten.
#
# Paths a walk should leave alone go in a [files] table, in gitignore syntax:
#
#   [files]
#   ignore = ["build/", "*.min.js", "!keep.md"]
#
# .gitignore is honoured too. A path named directly on the command line is
# scanned whatever the patterns say.

"""


def plural(n, word, suffix="s"):
    return "%d %s%s" % (n, word, "" if n == 1 else suffix)


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def is_exempt(ch):
    """True for characters that are never reported: printable ASCII, tab,
    newline, and Latin letters including every diacritic."""
    if ch == "\n" or ch == "\t":
        return True
    if "\x20" <= ch <= "\x7e":
        return True
    if not unicodedata.category(ch).startswith("L"):
        return False
    name = unicodedata.name(ch, "")
    # Ligatures are Latin-named but are paste artifacts, so they stay reported.
    return name.startswith("LATIN") and "LIGATURE" not in name


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


# Suggestions are comments only. The script never acts on one; it acts solely on
# an `action` you have written yourself.
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
        0xFB01: ('action = "replace", to = "fi"', "ligature, usually a PDF paste"),
        0xFB02: ('action = "replace", to = "fl"', "ligature, usually a PDF paste"),
    }
    if cp in typography:
        return typography[cp]
    if cp == 0x000D:
        return ('action = "delete"',
                "stray CR; a CR that ends a CRLF line is never touched")
    if cp == 0xFFFD:
        return ('action = "delete"',
                "mojibake marker - evidence of prior data loss, inspect the source")
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
        return ('action = "delete"', "combining mark - text may be NFD-decomposed")
    return (None, None)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

class ConfigError(Exception):
    pass


def load_config(path, base=None):
    """Return ({char: entry}, [ignore pattern]). Raises ConfigError on anything
    malformed."""
    if not path.exists():
        return {}, []
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
                    '%s: [chars.%s] action = "replace" needs a non-empty `to`; '
                    'use action = "delete" to remove the character'
                    % (path, key))
            if chr(cp) in to:
                # Each run would reintroduce the character and grow `to` again,
                # so the file could never reach a fixed point.
                raise ConfigError(
                    "%s: [chars.%s] to = %s contains the character it replaces, "
                    "so repeated runs would never converge"
                    % (path, key, toml_string(to)))

        collapse = entry.get("collapse", False)
        if not isinstance(collapse, bool):
            raise ConfigError(
                "%s: [chars.%s] collapse = %r must be true or false"
                % (path, key, collapse))

        ch = chr(cp)
        # An exempt character is never reported, so acting on one would change
        # bytes the report never showed. Refuse rather than corrupt silently.
        if action in ("delete", "replace") and is_exempt(ch):
            raise ConfigError(
                "%s: [chars.%s] %s is exempt and never reported, so it cannot "
                "be %sd" % (path, key, name_of(ch), action))
        if ch in out:
            raise ConfigError(
                "%s: [chars.%s] duplicates an earlier entry for the same "
                "character" % (path, key))
        out[ch] = {"action": action, "to": to, "collapse": collapse}
    return out, patterns


def load_patterns(path, files):
    """Validate a [files] table and return its ignore patterns, unparsed.

    Unknown keys are refused rather than ignored: unlike a [chars."U+XXXX"]
    entry, which carries name/cat/seen metadata the tool wrote itself, there is
    nothing here that a typo could plausibly be.
    """
    if files is None:
        return []
    if not isinstance(files, dict):
        raise ConfigError("%s: `files` must be a table holding `ignore`, not %s"
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


def append_entries(path, chars, counts):
    """Append undecided entries for `chars`. Never rewrites existing bytes, so
    hand edits, comments and ordering survive.

    Holds an exclusive lock and re-reads under it, so two concurrent runs cannot
    append the same table twice and brick the ledger.
    """
    try:
        # The global ledger lives under ~/.config/charck/, which may not exist
        # on a first run.
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ConfigError("%s: %s" % (path, exc.strerror))
    with fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.seek(0)
        existing = fh.read()
        present = {m.group(1).upper() for m in TABLE_RE.finditer(existing)}
        fresh = [ch for ch in chars if key_of(ch) not in present]
        if not fresh:
            return []
        parts = []
        if not existing:
            parts.append(HEADER)
        elif not existing.endswith("\n"):
            # A hand-edited file may lack a final newline; appending straight on
            # to that last line would produce invalid TOML.
            parts.append("\n")
        parts.append("\n".join(render_entry(ch, counts[ch]) for ch in fresh))
        parts.append("\n")
        fh.seek(0, os.SEEK_END)
        fh.write("".join(parts))
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


def scan_text(text):
    """Yield (lineno, col, char, body) for every non-exempt character."""
    for lineno, line in iter_lines(text):
        body = line_body(line)
        for col, ch in enumerate(body, start=1):
            if not is_exempt(ch):
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
    configured, results do not depend on entry order, and repeated runs converge.
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
            # At the start of a line the leading run is indentation, not spacing
            # around the character, so keep it and only collapse to its right.
            indent = got[:len(got) - len(got.lstrip(" \t"))]
            return indent + to.lstrip(" \t")
        return to

    return pattern.subn(repl, text)


def write_atomic(path, text):
    # Follow symlinks: rewrite the file the content was read from, and leave the
    # link itself in place.
    target = Path(os.path.realpath(path))
    mode = target.stat().st_mode
    # mkstemp, not a fixed name: a predictable sibling could already exist (and
    # be clobbered) or be a symlink pointing somewhere else. It is created 0600,
    # so the content is never briefly world-readable.
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
            segment = (i == 0 or text[i - 1] == "/") and (j == n or text[j] == "/")
            if j - i >= 2 and segment:
                if j == n:
                    out.append(".*")           # trailing **: everything below
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


def make_rule(pattern, base, where, label):
    """Compile one pattern. Raises ConfigError on anything unusable."""
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
            fault = "has a . or .. segment, and patterns are relative to the " \
                    "directory of the file they are written in"
    if fault is None and anchored and base is None:
        fault = ("is anchored, and the global ledger has no directory to "
                 "anchor it to; a pattern with a slash belongs in a project %s"
                 % LOCAL_NAME)
    if fault is not None:
        raise ConfigError("%s: ignore pattern %s %s"
                          % (where, toml_string(pattern), fault))

    try:
        compiled = re.compile(("" if anchored else "(?:.*/)?")
                              + pattern_regex(text))
    except (re.error, ValueError) as exc:
        raise ConfigError("%s: ignore pattern %s is not usable (%s)"
                          % (where, toml_string(pattern), exc))
    return {"re": compiled, "neg": negated, "dir": dir_only,
            "pat": pattern, "src": label, "file": str(where)}


def rule_group(patterns, base, where, label, bad=None):
    """Return (prefix, rules): `prefix` is what a path must start with for the
    group to apply at all, and None means the rules match at any depth.

    With `bad` given an unusable pattern is collected there instead of raising.
    A .gitignore is not ours to validate, and one odd line in someone else's
    file must not stop the run; a pattern in our own ledger still does.
    """
    rules = []
    for pattern in patterns:
        try:
            rules.append(make_rule(pattern, base, where, label))
        except ConfigError:
            if bad is None:
                raise
            bad.append((pattern, where))
    return (None if base is None else base.as_posix().rstrip("/") + "/", rules)


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
            groups.append(rule_group(read_ignore_file(path, bad), directory, path,
                                     "gitignore", bad))
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
    # Outside a work tree git applies no ignore rules at all, so neither do we;
    # a stray .gitignore in a tarball is not policy anyone recorded. A walk that
    # starts outside one can still descend into a repository of its own.
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
                groups = groups + [rule_group(read_ignore_file(local, ign["bad"]), here,
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
            print("error: no such file or directory: %s" % raw, file=sys.stderr)
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
                         "(default: %s overridden per character by the nearest "
                         "%s at or above the working directory)"
                         % (global_config(), LOCAL_NAME))
    ap.add_argument("--ext", metavar="LIST", default="",
                    help="restrict a directory walk by extension, e.g. .md,.toml")
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
    config, source, groups = merge_layers(layers)

    defaults = rule_group(DEFAULT_IGNORE, None, "built-in defaults", "default")
    ign = {
        "head": [] if args.no_ignore else [defaults],
        "tail": [] if args.no_ignore else groups,
        "git": not (args.no_ignore or args.no_gitignore),
        "bad": [],                 # unusable .gitignore lines, shown under -v
    }

    if args.list:
        for path, base in layers:
            print("# %s" % path)
        if config:
            print("%-9s %-42s %-16s %s" % ("CODE", "NAME", "DECISION", "FROM"))
            for ch in sorted(config, key=ord):
                print("%-9s %-42s %-16s %s"
                      % (key_of(ch), name_of(ch)[:42], describe(config[ch]),
                         "local" if source[ch].name == LOCAL_NAME else "global"))
        else:
            print("no decisions recorded in %s" % config_path)
        print()
        print("%-9s %s" % ("FROM", "IGNORE"))
        for _, rules in ign["head"] + ign["tail"]:
            for rule in rules:
                print("%-9s %s" % (rule["src"], rule["pat"]))
        # Which .gitignore files apply depends on what is being walked, and
        # --list is given no path, so they cannot be listed here.
        print("later patterns win; .gitignore is read during a walk and is not "
              "listed here")
        return 0

    if not args.paths:
        ap.error("no PATH given")
    if args.fix and args.dry_run:
        ap.error("--dry-run and --fix are mutually exclusive")

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
        got = list(scan_text(text))
        if got:
            cache[path] = text
        for lineno, col, ch, body in got:
            findings.append((path, lineno, col, ch, body, is_self))
            counts[ch] = counts.get(ch, 0) + 1

    # Characters with no entry yet get appended as undecided, always to a local
    # ledger; the global one is never written to.
    unknown = [ch for ch in sorted(counts, key=ord) if ch not in config]
    appended = []
    created_ledger = False
    if unknown and not args.no_append:
        created_ledger = not config_path.exists()
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
                print("%s%s" % (path, "   [SELF - not rewritten]" if is_self else ""))
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
              % (plural(changed_chars, "character"), plural(changed_files, "file")))
    for path, reason in failures:
        print("  ERROR could not write %s: %s" % (path, reason), file=sys.stderr)
    if blocked:
        # Printed even under -q: silently declining to apply a recorded decision
        # is exactly the thing a caller must not miss.
        print("  %s left in self-referencing files (%s); name the file directly "
              "to rewrite it"
              % (plural(len(blocked), "decided character"),
                 ", ".join(key_of(c) for c in blocked)))
    if created_ledger:
        print("  created %s" % config_path)
    if undecided:
        n = len(undecided)
        print("  %d character%s need%s a decision - %s %s"
              % (n, "" if n == 1 else "s", "s" if n == 1 else "",
                 "re-run without --no-append to record them in"
                 if args.no_append else "edit", config_path))

    nfiles = len({f[0] for f in findings})
    # An ignored directory counts once and its contents are never enumerated,
    # which is the point of pruning rather than filtering.
    print("  %s in %s  (%d scanned, %d skipped, %d ignored)"
          % (plural(len(findings), "occurrence"), plural(nfiles, "file"),
             scanned, skipped, len(ignored)))

    if failures:
        return 2
    if args.fix:
        return 1 if (undecided or blocked) else 0
    outstanding = [f for f in findings
                   if config.get(f[3], {}).get("action", "") != "ignore"]
    if outstanding and not undecided and not args.quiet:
        print("  all decided; run --fix to apply")
    return 1 if outstanding else 0


def cli(argv=None):
    """Console entry point: turns exceptions into the documented exit codes.

    Both `python3 charck.py` and the installed `charck` command go through here,
    so an installed copy reports a bad ledger as exit 2 rather than a traceback.
    """
    try:
        return main(argv)
    except ConfigError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(cli())
