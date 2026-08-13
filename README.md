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

Everything outside printable ASCII, with a single exemption: Latin letters
carrying diacritics. `á`, `š` and `ř` are letters you meant to type, and flagging
them on every run would bury the signal. Ligatures such as `ﬁ` and fullwidth
forms such as `Ａ` are still reported, because those are paste artifacts rather
than letters.

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

## Usage

```sh
charck content/            # report, and append new characters as undecided
$EDITOR .charck.toml       # record your decisions
charck content/            # confirm what will change
charck --fix content/      # apply
```

A path may be a file or a directory, and directories are walked recursively.
Dot-directories, build output and anything that is not a UTF-8 text file are
skipped.

```
--fix              apply decided actions, rewriting files in place
--no-append        report only, and do not add entries to the ledger
--list             print the merged ledger as a decision table
--config PATH      use only this ledger, ignoring both layers
--ext .md,.toml    restrict a directory walk by extension
-v / -q            show ignored characters / summary only
```

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

Two things `charck` will not touch, whatever the ledger says. It never rewrites
itself or a ledger it is currently reading, since that would corrupt the decisions
the run is acting on. And it refuses a `replace` whose `to` contains the character
being replaced, because repeated runs could never converge on a fixed point.

The tool is under git in every case I use it, and `git diff` is the real review
step. Run without `--fix` first and read the report.

## Requirements

Python 3.11 or newer, for `tomllib`. Standard library only, no dependencies.

## Install

```sh
pip install .
```

Or copy `charck.py` somewhere on your `PATH` and run it directly. It is a single
file with no imports outside the standard library, so that works fine.

## Tests

```sh
python3 tests/test_charck.py
```

71 cases covering the reporting contract, the ledger layering, and the failure
modes above. It exits non-zero if anything fails.

## Disclaimer

I wrote this for my own files and my own habits. It does what I need it to do,
and I have not tried it against anything much stranger than my own writing. If it
eats something of yours, you had a backup, right?
