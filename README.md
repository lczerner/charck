# charck

`charck` checks text files for characters that are not plain ASCII, tells you
exactly what and where they are, and removes or replaces them according to
decisions you record once.

## The problem

You cannot see a no-break space. That is the whole difficulty.

Invisible characters get into Markdown and configuration files by way of
copy-paste from web pages, PDFs, word processors and, increasingly, LLM output.
They survive review, because there is nothing to review - the file looks right.
Then something breaks, usually somewhere unhelpful:

- a no-break space (`U+00A0`) quietly defeats a `grep` for a plain space
- a byte order mark (`U+FEFF`) at offset `0` corrupts YAML front matter
- a zero width space (`U+200B`) splits a word for a search indexer
- a run of variation selectors (`U+E0100` to `U+E01EF`, 240 of them) carries text
  that renders as nothing

The point of `charck` is visibility first. What to do about what it finds is your
decision, and it is recorded rather than guessed.

## What gets reported

Everything outside printable ASCII, with a single exemption: the letters your
language writes with. `á`, `š` and `ř` are letters a Czech writer meant to type,
and flagging them on every run would bury the signal. Ligatures such as `ﬁ` and
fullwidth forms such as `Ａ` are still reported, because those are paste artifacts
rather than letters. Which letters are exempt is the subject of
[Your language](#your-language) below.

Note that a rule based on Unicode general category alone is not enough here, and
this is worth spelling out, because it is where the naive version of this tool
goes wrong:

- all 260 variation selectors are category `Mn`, a *mark*
- the Hangul fillers `U+115F`, `U+1160`, `U+3164` and `U+FFA0` are category `Lo`,
  that is *letters*, and they render as blank space
- `U+2800` braille blank and `U+FFFD`, the replacement character, are category
  `So`, *symbols*

So `charck` works the other way around. It reports everything that is not on the
exemption list, which makes coverage a property of the design rather than of how
many special cases we remembered.

## Your language

The exempt set comes from your locale. `cs_CZ.UTF-8` exempts the fifteen letters
Czech uses and no others, so a Polish `ł` or a German `ß` in Czech prose is still
a finding. A locale replaces the set rather than adding to it: under `ru_RU` the
exempt letters are Cyrillic, and a stray `é` is reported like anything else.

Languages an alphabet cannot describe get their whole script instead. Japanese is
kana plus 97668 Han ideographs, which is not a list anyone can write down, so
`ja_JP` exempts those scripts and the punctuation that goes with them. `U+3000
IDEOGRAPHIC SPACE` is deliberately still reported: it is invisible whitespace,
which is the thing this tool exists to find.

Where the answer comes from, in order:

| source | |
| --- | --- |
| `--lang LOCALE`, `--no-lang` | this run only |
| `[locale]` in the ledger | a local table replaces the global one outright |
| `$LC_ALL`, `$LC_CTYPE`, `$LANG` | recorded to the ledger on first use |
| the Latin script | what `charck` exempted before it asked |

A locale read from the environment is written into the ledger as the run goes, so
later runs no longer depend on a variable. An alphabet is recorded as the letters
themselves:

```toml
# .charck.toml
[locale]
exempt = "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
```

That is also how you correct it. Real prose carries foreign names, and the first
run over a tree will ask about the `ö` in Schrödinger. Add the letters you would
rather not hear about again to that one string and the tool's own table stops
mattering. `lang = "none"` exempts nothing beyond ASCII, and so does `--no-lang`.

Two things outrank the locale. Printable ASCII, tab and newline are never
reported whatever any of this says. And a character you have decided to `delete`
or `replace` is always reported, so that `--fix` never changes bytes the report
did not show. An undecided entry does not count: `charck` writes one for every
character it has ever seen, so an entry means nothing until you fill in an
action, and a letter you add to `exempt` goes quiet even though the ledger still
carries the entry that first asked about it.

## Detection and policy are separate

The tool reports. The ledger decides.

Findings come with codepoint, name, count and exact line and column. Decisions
live in a TOML ledger holding one entry per character. Newly-seen characters are
appended automatically, each one undecided, and you fill in what should happen:

| action | effect |
| --- | --- |
| `""` | undecided: reported, never modified |
| `"delete"` | remove the character |
| `"replace"` | substitute the `to` value |
| `"ignore"` | intentionally keep it, and stop reporting it |

Nothing is decided for you. Entries arrive with a `# suggested:` comment where
the handling is well understood, but `charck` acts only on an `action` you have
written yourself. Until you fill one in, `--fix` leaves that character alone.

## Two ledgers, layered per character

```
~/.config/charck/charck.toml   your decisions, applied everywhere
./.charck.toml                 this project's exceptions
```

The global ledger is always read. The local one is found by walking up from the
working directory, and it overrides the global **only for the characters it
mentions**. Everything else falls through. A project that wants a plain `-` where
you normally write `->` says that much and inherits the rest:

```toml
# .charck.toml
[chars."U+2192"]
action = "replace"
to     = "-"
```

**The global ledger is never written to.** It is yours to edit by hand, and
nothing `charck` discovers is added to it behind your back. New characters always
go to the local ledger, which is created in the working directory if none is in
scope. It says so when it creates one.

`charck --list` prints the merged result and which layer each decision came from.
`--config PATH` ignores both layers and reads and appends to exactly the file you
name.

## Ignoring files

What a walk should leave alone goes in the same ledger, in gitignore syntax:

```toml
# .charck.toml
[files]
ignore = [
  "build/",
  "*.min.js",
  "content/vendor/**",
  "!content/vendor/notes.md",
]
```

The syntax is gitignore's, and so are its rules. A trailing `/` matches only
directories, a leading or embedded `/` anchors the pattern to the directory of
the file it is written in, `*` stops at a `/` and `**` does not, and a leading
`!` puts something back. Of two patterns that both match, the later one decides.

Five sources are read, in this order:

```
built-in defaults -> .gitignore -> global ledger -> local ledger -> --exclude
```

The built-in defaults are dot-files and dot-directories, `node_modules`,
`__pycache__`, `.venv`, `venv`, `target` and `dist`. They are ordinary patterns
rather than a hardcoded rule, so `ignore = ["!.github/"]` brings that one back.

`.gitignore` is read where git would read it: every file from the top of the work
tree down to the directory being walked, plus `.git/info/exclude`, with the
deeper file winning over the shallower one. Outside a work tree nothing is read,
since git would apply nothing there either. `--no-gitignore` turns that off,
`--no-ignore` turns off every pattern including the defaults, and `-v` prints
each ignored path with the pattern that caught it and the file it came from.

### Excluding from the command line

The first run over a tree is the one with no ledger to record anything in, which
is a poor moment to find out that `build/` holds four hundred generated files.
`--exclude` takes one pattern, in the same syntax, and repeats:

```sh
charck --exclude build/ --exclude '*.min.js' --exclude 'content/vendor/**' .
```

It outranks everything recorded, `!` patterns included, and it is written to the
ledger as the run goes, so the next run needs no flag. A pattern the ledger
already lists is not added twice, and `--no-append` applies it to this run only.

A pattern anchors to the directory you are standing in rather than to the tree
you are scanning, so `--exclude /build/` run from `content/` means
`content/build` wherever the walk goes. The ledger it gets recorded in may be
somewhere else, and an anchored pattern is rewritten on the way in so that it
goes on meaning the same place: from `/proj/sub`, with the ledger at `/proj`,
`/build/` is recorded as `/sub/build/`. The run prints that when it happens. A
pattern without a slash matches at any depth and is recorded exactly as typed.

Two things it will not do. A ledger whose `ignore` array cannot be located for
certain, which is `files.ignore` written as a dotted key or as an inline table,
is left alone: the pattern still applies to the run, it is printed for you to
paste in, and the run exits `1` so that nothing reads as clean when it is not.
And a pattern that could not go on meaning the same thing is refused with exit
`2` before the walk rather than applied and then quietly reinterpreted. That
covers an anchored pattern when the ledger is not above the directory you are
standing in, and any pattern at all when the tree you are scanning is not under
the ledger either, since a recorded pattern only ever applies below its own
file. `--no-append` is the way through both.

The array is the one part of a ledger `charck` edits rather than appends to. It
re-reads the file under a lock, builds the new text, parses it and compares the
whole document against the one it started from, and only then writes it out to a
temporary file and renames it over the original. A write that fails halfway
leaves your decisions where they were.

Matching is case-sensitive, even where the filesystem is not. On macOS `build/`
does not ignore a directory named `Build`, though git, which sets
`core.ignorecase` on such a volume, would ignore both.

Now the limits, of which there are five.

**An ignored directory is never descended into**, so `!build/keep.md` cannot
reach a file under an ignored `build/`. That is gitignore's behaviour too, and it
is what makes pruning cheap, because the contents are never listed at all.
Negate the directory instead, or name the file on the command line.

**A path named directly on the command line is never ignored.** `charck
build/x.md` scans that file and `charck build/` walks that directory, whatever
the patterns say. It is the same escape hatch that reaches a file `charck` would
otherwise refuse to rewrite.

**`.git` is never walked into**, whatever the patterns say and whatever
`--no-ignore` says. A repository is full of text files that are not prose, and a
`--fix` over `config`, `HEAD` or a loose ref would corrupt the repository
itself. A walk cannot reach one by any spelling: not as a directory it meets,
not as the directory you point at, and not through a symlink that leads back
inside. Pointing `charck` at a `.git` is refused with exit `2` rather than
quietly scanning nothing. Naming a single file inside one still works, because
a path you name is a path you meant.

**The global ledger takes only patterns without a slash.** Anchoring is relative
to the directory of the file the pattern is written in, and `~/.config/charck/`
is not your project. An anchored pattern there is a configuration error rather
than a rule that quietly never matches, so put it in the project's
`.charck.toml`.

**Three things git does that `charck` does not.** It does not read
`core.excludesFile`. It has no idea which files are tracked, so a file that git
tracks despite matching a pattern is skipped here anyway. And where `.git` is a
file rather than a directory, which is what a linked worktree and a submodule
have, it does not follow that file to the repository's `info/exclude`. The
`.gitignore` files themselves are read normally in all three cases, and the
error is always toward scanning more than git would.

## Usage

```sh
charck content/            # report, and append new characters as undecided
$EDITOR .charck.toml       # record your decisions
charck content/            # confirm what will change
charck --fix content/      # apply
```

A path may be a file or a directory, and directories are walked recursively. What
the ignore patterns exclude is left out, and so is anything that is not a UTF-8
text file. A symlinked directory met during a walk is not descended into, as git
does not descend into one either, so a subtree reached only through a link is
not covered by a run over its parent. Name it and it is walked.

```
--fix              apply decided actions, rewriting files in place
--no-append        report only, and do not add entries to the ledger
--list             print the merged ledger as a decision table
--config PATH      use only this ledger, ignoring both layers
--lang cs_CZ       exempt this language's letters, instead of asking $LANG
--no-lang          exempt nothing beyond printable ASCII
--ext .md,.toml    restrict a directory walk by extension
--exclude build/   leave this out of a walk, and record it in the ledger
--no-ignore        apply no ignore patterns at all, defaults included
--no-gitignore     do not read .gitignore, but keep the ledger's patterns
-v / -q            show what was ignored or skipped / summary only
```

A file that cannot be read as UTF-8 text is skipped, and so is one an ignore
pattern excludes. Both are counted in the last line of the report and neither is
named unless you ask with `-v`, since a content tree with a few hundred images in
it would otherwise bury the findings under a line per image.

Exit code `0` means clean, `1` means there is something to look at or apply, and
`2` means an operational error. The `1` is what makes the report usable as a build
gate.

## Before you run it with `--fix`

This section is the one worth reading twice. `--fix` rewrites files in place, and
there are four cases where the result may not be what you assumed.

**Deleting a space-like character glues words together.** `5` `U+00A0` `km`
becomes `5km` if you decide `delete`, which is a worse corruption than the one you
started with. For anything in category `Zs` the suggested action is
`replace` with a plain space, and it is suggested for that reason.

**Unassigned codepoints depend on your Python.** 819,533 codepoints are
unassigned in Unicode 16.0.0, which is what Python 3.14 reports. A character
assigned in a later Unicode release reads as unassigned here, so a blanket
`delete` decision on category `Cn` will remove characters that are simply newer
than your interpreter. Those findings are labelled in the report for that reason.

**The private use area is where Nerd Font glyphs live.** Category `Co` covers
`U+E000` to `U+F8FF`. Point `charck` at a shell configuration with a `delete`
decision there and your prompt icons are gone.

**Files are replaced atomically, which breaks hardlinks.** A rewritten file gets a
new inode, so any other name that shared the old one keeps the old content.
Extended attributes and ACLs are not carried over either. Symlinks, on the other
hand, are followed, so the real file is rewritten and the link is left intact.

Three things `charck` will not touch, whatever the ledger says. It never rewrites
itself or a ledger it is currently reading, since that would corrupt the decisions
the run is acting on. It never looks inside `.git`. And it refuses a `replace`
whose `to` contains the character being replaced, because repeated runs could
never converge on a fixed point.

The tool is under git in every case I use it, and `git diff` is the real review
step. Run without `--fix` first and read the report.

## Requirements

Python 3.11 or newer, for `tomllib`. Standard library only, no dependencies.

`make install` also needs `pipx`. Running `charck.py` straight out of the
checkout does not.

## Install

```sh
make install
```

That goes through `pipx`, which builds the project into its own virtualenv under
`~/.local/pipx/venvs/charck` and links the command into `~/.local/bin`. Nothing
lands outside `$HOME`, and no recipe in the `Makefile` runs `sudo`. To undo it,
`make uninstall`.

You need `~/.local/bin` on your `PATH`. `pipx ensurepath` adds it.

What pipx installs is a copy, so editing `charck.py` in the checkout does not
change the installed command. Run `make install` again after a change. If you
would rather have the checkout itself be what runs, `pipx install --editable .`
does that, at the price of your scripts picking up whatever half-finished edit is
in the tree.

Or copy `charck.py` somewhere on your `PATH` and run it directly. It is a single
file with no imports outside the standard library, so that works fine. You give
up the version metadata and the uninstall record, which for a personal tool may
not be worth much anyway.

## Tests

```sh
make test
```

The suite is pytest and lives in `tests/`, one module per area: rewriting,
reporting, the ledger and its layers, the ignore rules, `.gitignore`, `--exclude`
and the `Makefile`. `make test` builds the `.venv` first if it is not there yet
and runs the suite from it, so the first run needs `pip` and the network;
`.venv/bin/pytest tests/test_ignore.py` afterwards runs a single module.

A few cases skip themselves rather than lie: the `Makefile` ones on a checkout
with no `make`, and the ones reading a file for its licence header when that
file is not there at all, which is how the sdist ships. Two `.gitignore` cases
skip when `TMPDIR` is inside a git repository that would otherwise have a say in
the result, and the permission ones skip when you are root.

## Lint

```sh
make venv
make lint
```

`make venv` builds a virtualenv in `.venv` from the `dev` extra in
`pyproject.toml`, and `make lint` runs `flake8` from it over `charck.py` and
`tests/`. `flake8` and `pytest` are the only development dependencies there are;
the tool itself still needs nothing outside the standard library.

## License

MIT. The full text is in `LICENSE`, and `charck.py`, the test suite, the
`Makefile` and `pyproject.toml` each carry an `SPDX-License-Identifier: MIT`
line, so a file that gets copied out of the tree still says what it is.

Copyright 2026, Lukáš Czerner <lukas@czerner.cz>

## Disclaimer

I wrote this for my own files and my own habits. It does what I need it to do,
and I have not tried it against anything much stranger than my own writing. If it
eats something of yours, you had a backup, right?
