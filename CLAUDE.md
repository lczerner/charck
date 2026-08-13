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
charck.py              the entire tool, one module, ~1685 lines, stdlib only
tests/test_charck.py   standalone regression suite, ~1025 lines, 196 cases (190 without make)
pyproject.toml         setuptools, py-modules = ["charck"], console script charck = charck:cli
Makefile               help (default), test, install, uninstall, clean; GNU make 3.81 compatible
README.md              user-facing docs, written in the author's voice (see Voice below)
.gitignore             ignores .charck.toml, .*.charck-tmp, and the usual Python noise
```

### Map of `charck.py`

Section comments (`# ---- name ----`) mark the boundaries. In file order:

| Region | Functions | Purpose |
|---|---|---|
| Config discovery | `xdg_config_home`, `global_config`, `find_local_config`, `resolve_layers`, `merge_layers` | Locate and layer the two ledgers |
| Module constants | `LOCAL_NAME`, `SELF_FILES`, `ACTIONS`, `KEY_RE`, `TABLE_RE`, `CONTEXT`, `HEADER` | |
| Classification | `is_exempt`, `key_of`, `name_of`, `suggest` | What is reported, and what the ledger suggests |
| Config | `ConfigError`, `load_config`, `load_patterns`, `toml_string`, `render_entry`, `array_end`, `splice`, `ignore_insert`, `open_ledger`, `read_ledger`, `append_patterns`, `append_entries` | Read, validate, append to and record a pattern in a ledger |
| Scanning | `read_source`, `iter_lines`, `line_body`, `scan_text`, `safe_context`, `render` | Find findings and display them |
| Rewriting | `build_pattern`, `apply_decisions`, `write_atomic` | Apply decisions and write files |
| Ignoring | `DEFAULT_IGNORE`, `ALWAYS_PRUNE`, `POSIX_CLASS`, `class_regex`, `pattern_regex`, `make_rule`, `rule_group`, `record_form`, `ignored_by`, `read_ignore_file`, `inside_git`, `git_root`, `git_groups` | gitignore-syntax patterns, and where they come from |
| Walking | `walk_tree`, `collect` | Expand paths into targets |
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
- **`append_patterns` is the one exception, and it goes through `write_atomic`.** A
  pattern has to go inside the `[files] ignore` array, which no append can do, and
  a rewrite in place would put every decision after the insertion point at the
  mercy of a short write. So it builds the new text, re-parses it and compares the
  whole document against the one it came from, then writes it the way every other
  file here is written: temporary file, `fsync`, rename. A ledger it cannot read
  with certainty, `files.ignore` as a dotted key or an inline table, is left alone
  and the pattern is printed instead. Recording is exit `1` when it is declined,
  never a silent `0`.
- **A `--exclude` means the same thing after it is written down.** It anchors to
  the working directory, and `record_form` rewrites an anchored one for the
  directory of the ledger it lands in. Where that rewrite is impossible, or where a
  walk root is not under the ledger the pattern would go in, the run is exit `2`,
  never a pattern that means somewhere else, or nothing at all, on the next run.
- **Writes are atomic and follow symlinks.** `tempfile.mkstemp` (not a predictable
  name), `os.fsync`, `os.chmod` from the original, then `os.replace` onto the
  realpath.
- **`scan_text` splits only on `\n`.** Never `str.splitlines()`, which also splits
  on `U+2028`, `U+2029`, `U+0085`, `\x0b` and `\x0c`, the very characters we must
  report.
- **Decode with plain `utf-8`, never `utf-8-sig`,** so a leading BOM surfaces as a
  normal `U+FEFF` finding at line 1 col 1.
- **A walk never reaches `.git` by any spelling**, whatever a pattern or
  `--no-ignore` says. Three guards, and a review found the tree by getting past
  the first two: `ALWAYS_PRUNE` by name, for directories and for the `.git` file
  a worktree has; `inside_git` on the walk root, so naming one is exit `2`
  rather than a silent full scan; and `inside_git` on every symlinked file,
  since `write_atomic` follows a link to the real file and would have rewritten
  a loose ref while the report named the link. A single file named directly is
  still scanned. A `--fix` in there corrupts the repository.
- **A path named directly on the command line is never ignored.** Patterns apply
  to what a walk discovers, not to what the caller asked for by name. It is the
  same `direct` flag that reaches a `SELF` file.
- **Ignoring happens before reading**, in `walk_tree`, so the report and the
  rewrite still see one identical set of files.
- **An anchored pattern needs a directory to anchor to.** Every rule is relative
  to the file it was written in; the global ledger has no such directory, so an
  anchored pattern there is a `ConfigError` rather than a rule that never fires.
- **Our own ledger is validated, someone else's `.gitignore` is not.** A pattern
  `charck` cannot compile is exit `2` from a ledger and a `-v` line from a
  `.gitignore`. One odd line in a repository's own file must not stop the run.
- **Exit codes:** `0` clean or fully applied, `1` something to look at or apply,
  `2` operational error. Never let an exception escape as a traceback.

## Commands

```sh
make test                              # or: python3 tests/test_charck.py
make help                              # the target list, and the default goal
python3 -m py_compile charck.py        # syntax check
python3 charck.py --no-append README.md   # dogfood: must report only U+FB01 and U+FF21
pipx run build --wheel                 # packaging check, without installing `build`
```

There is no linter, formatter or CI configured. The test suite is the gate.

`make install` goes through pipx, so the tool lands in its own virtualenv under
`~/.local/pipx/venvs` with the command linked into `~/.local/bin`. Nothing is
written outside `$HOME`, and `make uninstall` undoes it. Section `T` of the suite
asserts that: no recipe may use `sudo` or a bare `pip install`. The Makefile
targets `python3` and GNU make 3.81, which is what macOS ships, so no `.ONESHELL`
and no `$(file ...)`.

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

### Committing

Committing is a separate, explicit request. Do not commit or push unless asked.
When asked, write the message to `../style/VOICE-COMMITS.md`.

**Never add a file to the repository without permission.** The one exception is
the obvious case: a file created in order to fulfil a request is part of that
work and gets staged with it. Everything else in the working tree stays
untracked, whatever `git status` happens to show. "Commit everything" means the
work under discussion, not a sweep of every untracked path. Scratch files, notes
and personal working copies are not yours to track. When a path is unclear, ask
before committing rather than committing it and flagging it afterwards.
