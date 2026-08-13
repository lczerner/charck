# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What charck is

`charck` is a command line tool that finds every character in a text file that is
not printable ASCII, reports where it is, and removes or replaces it according to
decisions recorded in a TOML ledger.

The design idea worth holding on to: **detection and policy are separate.** The
tool reports exhaustively and decides nothing. What happens to each character is
recorded by the user in a ledger and reused on every later run. Never add a
built-in rule that silently rewrites something the user has not decided.

Typical use is auditing prose and config files for invisible characters that
arrive by copy-paste: NBSP that defeats a `grep`, a BOM that corrupts YAML front
matter, zero-width spaces, variation selector runs, mojibake `U+FFFD`. It is
meant to be usable as a build gate (exit `1` when there is something to act on).

## Layout

```
charck.py              the entire tool, one module, ~880 lines, stdlib only
tests/test_charck.py   standalone regression suite, ~350 lines, 71 cases
pyproject.toml         setuptools, py-modules = ["charck"], console script charck = charck:cli
README.md              user-facing docs, written in the author's voice (see Voice below)
.gitignore             ignores .charck.toml, .*.charck-tmp, and the usual Python noise
```

### Map of `charck.py`

Section comments (`# ---- name ----`) mark the boundaries. In file order:

| Region | Functions | Purpose |
|---|---|---|
| Config discovery | `xdg_config_home`, `global_config`, `find_local_config`, `resolve_layers`, `merge_layers` | Locate and layer the two ledgers |
| Module constants | `LOCAL_NAME`, `SKIP_DIRS`, `SELF_FILES`, `ACTIONS`, `KEY_RE`, `TABLE_RE`, `CONTEXT`, `HEADER` | |
| Classification | `is_exempt`, `key_of`, `name_of`, `suggest` | What is reported, and what the ledger suggests |
| Config | `ConfigError`, `load_config`, `toml_string`, `render_entry`, `append_entries` | Read, validate and append to a ledger |
| Scanning | `read_source`, `iter_lines`, `line_body`, `scan_text`, `safe_context`, `render` | Find findings and display them |
| Rewriting | `build_pattern`, `apply_decisions`, `write_atomic` | Apply decisions and write files |
| Walking | `collect` | Expand paths into targets |
| CLI | `describe`, `build_parser`, `main`, `cli` | Argument handling, report, exit codes |

`main` returns an exit code. `cli` wraps it and converts exceptions into codes, and
both `python3 charck.py` and the installed `charck` command go through `cli`. Put
new top-level error handling there, not in the `__main__` block.

## Invariants

These were established by review and by real bugs. Breaking one is a regression
even if the test suite still passes, so add a test if you find a gap.

- **The report and the rewrite must agree.** `--fix` may only change characters
  the report showed. The live example is CRLF: `scan_text` skips the `\r` of a
  `\r\n` pair, so `build_pattern` matches `\r(?!\n)`. A ledger entry for an exempt
  character is a fatal config error for the same reason.
- **`to` is data, never a regex replacement template.** It goes through a `lambda`
  in `apply_decisions`. A literal backslash in `to` must survive verbatim.
- **Rewriting is a single left-to-right pass.** Replacement output is never
  re-examined, which is what makes results independent of ledger order and makes
  repeated runs converge. A `to` containing the character it replaces is rejected
  at load, since it could never reach a fixed point.
- **`collapse` never eats leading indentation.** Absorbing the indent of a line
  would change YAML and Markdown nesting.
- **The global ledger is never written to.** New characters go to a local
  `.charck.toml`, created in the working directory if none is in scope.
- **Every ledger in play is self-protected**, not only the one being appended to.
- **Appends are append-only.** `append_entries` never rewrites existing bytes, so
  hand edits, comments and ordering survive. It holds an `flock` and re-reads
  under it.
- **Writes are atomic and follow symlinks.** `tempfile.mkstemp` (not a predictable
  name), `os.fsync`, `os.chmod` from the original, then `os.replace` onto the
  realpath.
- **`scan_text` splits only on `\n`.** Never `str.splitlines()`, which also splits
  on `U+2028`, `U+2029`, `U+0085`, `\x0b` and `\x0c`, the very characters we must
  report.
- **Decode with plain `utf-8`, never `utf-8-sig`,** so a leading BOM surfaces as a
  normal `U+FEFF` finding at line 1 col 1.
- **Exit codes:** `0` clean or fully applied, `1` something to look at or apply,
  `2` operational error. Never let an exception escape as a traceback.

## Commands

```sh
python3 tests/test_charck.py           # full suite, exits non-zero on failure
python3 -m py_compile charck.py        # syntax check
python3 charck.py --no-append README.md   # dogfood: must report only U+FB01 and U+FF21
python3 -m build --wheel               # packaging check (needs `build` in a venv)
```

There is no linter, formatter or CI configured. The test suite is the gate.

`tests/test_charck.py` is a plain script, not pytest. It defines `check(label,
cond, extra)` and counts passes and failures. Sections are lettered (`A.`, `B.`,
...) and named after the failure mode they pin down. Add new cases in that style,
and always assert on real observed bytes rather than on the report text alone.

## Voice

Two guides live outside this repo, one per register. Read the relevant one before
writing; neither is optional, and they do not substitute for each other.

### Long-form and documentation: `../style/VOICE.md`

Applies to `README.md`, this file, and any prose written for a reader. The rules
that matter most:

- **Never emit an em-dash.** Use a spaced hyphen ` - `, a comma or a colon.
- Alternate long explanatory sentences with short blunt ones.
- Concrete over abstract. Numbers, codepoints, versions, real failure modes.
- Never oversell, least of all the tool itself.
- Present every solution with its cost or limit.
- Mostly `we` with some `I`, and `you` when addressing the reader directly.
- Avoid `delve`, `robust`, `crucial`, `seamless`, `leverage`, `utilize` and the
  rest of the never-write list in that guide.

### Commit messages: `../style/VOICE-COMMITS.md`

Applies to commit messages, cover letters and pull request descriptions. It is
self-contained, so read it instead of `VOICE.md`, not after it. In short:

- Subject is `subsystem: summary`, imperative, no full stop. Median 51
  characters, never past 65. Name the function, flag or test.
- The good subjects are **verb + object + the condition under which it matters**.
  If a subject stops after the object, the change was probably not understood.
- Match the subsystem prefix to what `git log` already uses in that area. This
  repo has no convention yet, so lowercase, with areas along the lines of
  `scan:`, `config:`, `fix:`, `walk:`, `tests:`, `docs:`.
- **Write a body**, wrapped at 72 columns, blank line after the subject. Only
  mechanical changes are exempt.
- **Say what is wrong before what you did.** Open on the current behaviour in
  present tense, hinge on `However`, then the fix as its own paragraph, often
  literally `Fix this by ...`.
- `we` is the voice in a commit body. `I` is rare there and normal in a cover
  letter.
- Never write `This patch`. No bullet-point bodies. No em-dash. No selling.
- Show the evidence when there is any: the failing test, the reproducer, the
  hexdump. Not otherwise.

### Comments

Code comments explain *why*, not *what*, and several existing ones record the bug
that motivated the line. Keep that habit.

## Workflow

Follow these steps in order for any non-trivial change. Do not skip ahead.

1. **Analyze and plan first. Never start with the work.** Understand the problem,
   read the relevant code, and produce a plan for approval. Wait for approval
   before touching anything.

2. **Once approved, create a new branch in a new git worktree** and implement
   there, not in the main checkout. Use best practices throughout. Match the
   surrounding style: `%`-formatting, no f-strings in `charck.py`, plain functions
   over classes, stdlib only.

3. **Do not commit yet.** Run every check the project has (see Commands). If
   anything fails, fix it and iterate until clean.

4. **Write new tests for the functionality you just created**, then run the whole
   suite again. Fix and iterate until it passes. Update the test count in
   `README.md` if it changed.

5. **Run an expert reviewer subagent** focused on finding design issues and bugs,
   and have it produce a report for you. Give it the concrete file paths, tell it
   to reproduce every finding before reporting, and tell it not to report style
   opinions.

6. **Review the findings yourself. Validate each one.** Fix what is real and what
   makes sense given the context and goal of this change. Reviewer findings are
   not automatically correct, and some will be wrong or out of scope. Say which.

7. **Report back**, covering:
   - what was done and how it was implemented
   - the decisions you made, and where the implementation differs from the plan
   - briefly, what the reviewer found, what you fixed, and what you did not, with
     the reason
   - any outstanding work, and any decision that has to be made on the human end

Committing is a separate, explicit request. Do not commit or push unless asked.
When asked, write the message to `../style/VOICE-COMMITS.md`.
