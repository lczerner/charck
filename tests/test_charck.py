#!/usr/bin/env python3
"""charck regression suite: behaviour contract + every reviewed failure mode.

Run standalone:  python3 tests/test_charck.py
"""
import os, subprocess, sys, shutil, stat, tempfile
from pathlib import Path

TOOL = str(Path(__file__).resolve().parent.parent / "charck.py")
ROOT = Path(tempfile.mkdtemp(prefix="charck-test-"))
passed = failed = 0

def run(*a, **kw):
    return subprocess.run([sys.executable, TOOL, *a], capture_output=True, text=True, **kw)

def check(label, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1; print("  PASS  %s" % label)
    else:
        failed += 1; print("  FAIL  %s   %s" % (label, extra))

def cfg(name, body):
    p = ROOT / name
    p.write_text(body, encoding="utf-8")
    return str(p)

def src(name, data):
    p = ROOT / name
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return p

print("== A. review finding: `to` is data, not a regex replacement template ==")
c = cfg("bs.toml", '[chars."U+2014"]\naction="replace"\nto="\\\\to"\ncollapse=true\n')
f = src("bs.md", "alpha — beta\n")
run("--config", c, "--fix", "-q", str(f))
check("backslash in `to` survives collapse verbatim",
      f.read_bytes() == b"alpha\\tobeta\n", repr(f.read_bytes()))

c = cfg("bs2.toml", '[chars."U+2014"]\naction="replace"\nto="C:\\\\dir"\ncollapse=true\n')
f = src("bs2.md", "x — y\n")
r = run("--config", c, "--fix", "-q", str(f))
check("`to` with \\d does not crash", "PatternError" not in r.stderr and r.returncode in (0, 1),
      r.stderr[-120:])
check("`to` with \\d written literally", f.read_bytes() == b"xC:\\diry\n", repr(f.read_bytes()))

print("\n== B. review finding: --fix must not touch what the scan never reported ==")
c = cfg("cr.toml", '[chars."U+2014"]\naction="replace"\nto=" - "\n'
                   '[chars."U+000D"]\naction="delete"\n')
f = src("cr.md", b"line one\xe2\x80\x94here\r\nline two\r\nline three\r\n")
run("--config", c, "--fix", "-q", str(f))
check("CRLF survives a U+000D delete decision",
      f.read_bytes().count(b"\r\n") == 3, repr(f.read_bytes()))
f2 = src("cr2.md", b"a\rb\r\nc\r\n")
run("--config", c, "--fix", "-q", str(f2))
check("lone CR still deleted, CRLF kept", f2.read_bytes() == b"ab\r\nc\r\n", repr(f2.read_bytes()))

r = run("--config", cfg("tab.toml", '[chars."U+0009"]\naction="delete"\n'), str(src("t.md", "a\tb\n")))
check("exempt char in config is a fatal error (exit 2)", r.returncode == 2, r.stdout + r.stderr)

print("\n== C. review finding: single pass, convergent, order-independent ==")
c = cfg("chain.toml", '[chars."U+2014"]\naction="replace"\nto="\u2026"\ncollapse=true\n'
                      '[chars."U+2026"]\naction="replace"\nto="..."\n')
f = src("chain.md", "a — b and \u2026 end\n")
run("--config", c, "--fix", "-q", str(f))
check("collapse output is not re-processed",
      f.read_text(encoding="utf-8") == "a\u2026b and ... end\n", repr(f.read_text(encoding="utf-8")))

c = cfg("conv.toml", '[chars."U+2014"]\naction="replace"\nto="\u2014-"\n')
f = src("conv.md", "a — b\n")
r = run("--config", c, "--fix", "-q", str(f))
check("self-containing `to` rejected at load (exit 2)", r.returncode == 2, r.stderr[-160:])
check("never converges -> file untouched", f.read_text(encoding="utf-8") == "a — b\n")

body = ('[chars."U+2014"]\naction="replace"\nto=" \u00b7 "\ncollapse=true\n',
        '[chars."U+00B7"]\naction="replace"\nto="X"\n')
outs = []
for i, order in enumerate([body, body[::-1]]):
    c = cfg("ord%d.toml" % i, "".join(order))
    f = src("ord%d.md" % i, "a — b\n")
    run("--config", c, "--fix", "-q", str(f))
    outs.append(f.read_text(encoding="utf-8"))
check("result independent of config entry order", outs[0] == outs[1], repr(outs))

print("\n== D. review finding: collapse must not eat indentation ==")
c = cfg("ind.toml", '[chars."U+2014"]\naction="replace"\nto=" - "\ncollapse=true\n')
f = src("ind.md", "    — nested\ntop — mid\n")
run("--config", c, "--fix", "-q", str(f))
check("leading indentation preserved",
      f.read_text(encoding="utf-8") == "    - nested\ntop - mid\n", repr(f.read_text(encoding="utf-8")))

print("\n== E. review finding: the live config is SELF-protected ==")
d = ROOT / "tree"; d.mkdir()
c = d / "my.toml"
c.write_text('[chars."U+2014"]\naction="replace"\nto="-"\n', encoding="utf-8")
src("tree/doc.md", "a — b\n")
before = c.read_bytes()
r = run("--config", str(c), "--fix", "-q", str(d))
check("config in the scanned tree is not rewritten", c.read_bytes() == before, repr(c.read_bytes()))
check("config still parses afterwards", run("--config", str(c), "--list").returncode == 0)

print("\n== F. review finding: config without a trailing newline ==")
c = cfg("nonl.toml", '[chars."U+2014"]\naction="ignore"')
f = src("nonl.md", "x \u2026 y\n")
run("--config", c, str(f))
r = run("--config", c, str(f))
check("append to newline-less config stays valid TOML", r.returncode != 2, r.stderr[-160:])

print("\n== G. review finding: symlinks are followed, not replaced ==")
real = src("real.md", "em — dash\n")
link = ROOT / "link.md"
link.symlink_to(real)
c = cfg("sym.toml", '[chars."U+2014"]\naction="replace"\nto="-"\ncollapse=true\n')
run("--config", c, "--fix", "-q", str(link))
check("symlink still a symlink", link.is_symlink())
check("target file was the one rewritten", real.read_text(encoding="utf-8") == "em-dash\n",
      repr(real.read_text(encoding="utf-8")))

print("\n== H. review finding: binary detection scans the whole file ==")
blob = src("blob.dat", b"A" * 9000 + b"\x00" + b"B" * 40)
r = run("--config", cfg("b.toml", ""), "-q", "-v", str(blob))
check("NUL past 8 KiB is still detected as binary", "binary (NUL byte)" in r.stdout, r.stdout[-160:])
# One line per unreadable file buries the findings in a tree full of images,
# so the reason is a -v question and the count carries it by default.
r = run("--config", cfg("b.toml", ""), "-q", str(blob))
check("a skipped file is not named without -v", "binary (NUL byte)" not in r.stdout,
      r.stdout[-160:])
check("...but the summary still counts it", "1 skipped" in r.stdout, r.stdout[-160:])

print("\n== I. review finding: FIFOs and non-regular files ==")
fifo_dir = ROOT / "fifo"; fifo_dir.mkdir()
os.mkfifo(fifo_dir / "pipe")
src("fifo/ok.md", "a — b\n")
try:
    r = subprocess.run([sys.executable, TOOL, "--config", cfg("f.toml", ""), "-q", str(fifo_dir)],
                       capture_output=True, text=True, timeout=10)
    check("FIFO in a walked tree does not hang", True)
except subprocess.TimeoutExpired:
    check("FIFO in a walked tree does not hang", False, "timed out")

print("\n== J. review finding: operational errors exit 2, not 1 ==")
for label, body in [("out-of-range key", '[chars."U+FFFFFF"]\naction="delete"\n'),
                    ("chars not a table", "chars = 5\n"),
                    ("duplicate character", '[chars."U+2014"]\naction="delete"\n'
                                            '[chars."U+02014"]\naction="delete"\n')]:
    r = run("--config", cfg("bad.toml", body), str(src("z.md", "a\n")))
    check("%s -> exit 2" % label, r.returncode == 2, "exit=%d %s" % (r.returncode, r.stderr[-100:]))
src("hasfind.md", "a — b\n")
# A missing parent is created, not an error: the global ledger lives under
# ~/.config/charck/, which will not exist on a first run.
missing = ROOT / "nope" / "deeper" / "x.toml"
r = run("--config", str(missing), str(ROOT / "hasfind.md"))
check("config in a missing directory is created", missing.is_file(),
      "exit=%d %s" % (r.returncode, r.stderr[-120:]))
check("...and the run proceeds normally", r.returncode == 1, "exit=%d" % r.returncode)
# But a parent that cannot be created is still an operational error.
blocked_dir = ROOT / "blocked"; blocked_dir.mkdir(); os.chmod(blocked_dir, 0o500)
try:
    r = run("--config", str(blocked_dir / "sub" / "x.toml"), str(ROOT / "hasfind.md"))
    check("uncreatable config parent -> exit 2", r.returncode == 2,
          "exit=%d %s" % (r.returncode, r.stderr[-120:]))
finally:
    os.chmod(blocked_dir, 0o700)
r = run("--config", str(ROOT), str(ROOT / "z.md"))
check("config that is a directory -> exit 2", r.returncode == 2, "exit=%d" % r.returncode)

print("\n== K. review finding: write failures reported, run continues, exit 2 ==")
ro = ROOT / "ro"; ro.mkdir()
src("ro/a.md", "a — b\n"); src("ro/b.md", "c — d\n")
c = cfg("ro.toml", '[chars."U+2014"]\naction="replace"\nto="-"\n')
os.chmod(ro, 0o500)
try:
    r = run("--config", c, "--fix", "-q", str(ro))
    check("unwritable dir -> exit 2 (not 1)", r.returncode == 2, "exit=%d" % r.returncode)
    check("write failure is reported", "could not write" in r.stderr, r.stderr[-160:])
finally:
    os.chmod(ro, 0o700)

print("\n== L. review finding: SELF skip is visible and affects exit code ==")
selfd = ROOT / "selfd"; selfd.mkdir()
(selfd / "charck.py").write_text("grep '\u2014' x\n", encoding="utf-8")
c = cfg("self.toml", '[chars."U+2014"]\naction="replace"\nto="-"\n')
r = run("--config", c, "--fix", "-q", str(selfd))
check("SELF skip announced even under -q", "self-referencing" in r.stdout, r.stdout[-200:])
check("SELF skip makes --fix exit 1, not 0", r.returncode == 1, "exit=%d" % r.returncode)
check("self file untouched", (selfd / "charck.py").read_text(encoding="utf-8") == "grep '\u2014' x\n")
r = run("--config", c, "--fix", "-q", str(selfd), str(selfd / "charck.py"))
check("naming it directly rewrites it (order-independent)",
      (selfd / "charck.py").read_text(encoding="utf-8") == "grep '-' x\n")

print("\n== M. review finding: render offsets and control sanitising ==")
c = cfg("r.toml", "")
f = src("r.md", b"ab\r\r\r\n")
out = run("--config", c, str(f)).stdout
check("multiple trailing CRs reported at distinct columns",
      "col   3" in out and "col   4" in out, out[:300])
f = src("vt.md", b"a\x0bb \xe2\x80\x94 c\n")
out = run("--config", cfg("r2.toml", ""), str(f)).stdout
check("raw control chars never echoed into the report", "\x0b" not in out, repr(out[:200]))

print("\n== N. pluralisation ==")
c = cfg("p.toml", '[chars."U+2014"]\naction="replace"\nto="-"\n')
f = src("p.md", "a — b\n")
out = run("--config", c, "--fix", "-q", str(f)).stdout
check("singular 'character fixed in 1 file'", "1 character fixed in 1 file" in out, out[-200:])

print("\n== O. --ext is an extension match ==")
e = ROOT / "extd"; e.mkdir()
src("extd/cmd", "a — b\n"); src("extd/real.md", "c — d\n")
out = run("--config", cfg("e.toml", ""), "--ext", "md", "-q", str(e)).stdout
check("--ext md does not match a file named 'cmd'", "1 scanned" in out, out[-200:])

print("\n== P. original plan verification, end to end ==")
c = cfg("full.toml", "")
fixture = ("\ufeff---\ntitle: Luk\u00e1\u0161 Czerner\n---\n"
           "Roughly 5\u00a0km away.\nA zero\u200bwidth word.\nsoft\u00adhyphen \x1b esc.\n"
           "sep\u2028here.\npua \ue000 and \ufffd moji.\nfill \u3164 braille \u2800 .\n"
           "heart \u2764\ufe0f ideo \u4e00\U000e0101.\nfam \U0001f468\u200d\U0001f469 here.\n"
           "lig \ufb01le \u00b7 dot.\narrow \u2192 quote \u2019s dash\u2014here.\n"
           "wave \U0001f44b done.\n\ttabbed stays.\n")
f = src("full.md", fixture)
r = run("--config", c, "-q", str(f))
check("bootstrap exits 1", r.returncode == 1)
text = Path(c).read_text(encoding="utf-8")
check("accented Latin never appended", "U+00E1" not in text and "U+0161" not in text)
check("tab/ASCII never appended", "U+0009" not in text)
check("invisibles appended", all(k in text for k in
      ("U+200B", "U+FEFF", "U+2028", "U+FE0F", "U+E0101", "U+3164", "U+2800", "U+FFFD")))
import re as _re
for key, act, to in [("U+00A0", "replace", " "), ("U+2028", "replace", "\\n"),
                     ("U+200B", "delete", ""), ("U+00AD", "delete", ""),
                     ("U+001B", "delete", ""), ("U+FEFF", "delete", ""),
                     ("U+E000", "delete", ""), ("U+FFFD", "delete", ""),
                     ("U+3164", "delete", ""), ("U+2800", "delete", ""),
                     ("U+FE0F", "delete", ""), ("U+E0101", "delete", ""),
                     ("U+200D", "delete", ""), ("U+00B7", "replace", "-"),
                     ("U+2014", "replace", " - "), ("U+1F44B", "ignore", "")]:
    blk = _re.search(r'(\[chars\."%s"\]\n(?:[^\[]*))' % _re.escape(key), text).group(1)
    new = blk.replace('action = ""', 'action = "%s"' % act).replace('to     = ""', 'to     = "%s"' % to)
    text = text.replace(blk, new)
Path(c).write_text(text, encoding="utf-8")
r = run("--config", c, "--fix", "-q", str(f))
a = f.read_text(encoding="utf-8")
check("NBSP -> space, words not glued", "Roughly 5 km away." in a)
check("ZWSP deleted", "A zerowidth word." in a)
check("U+2028 -> newline", "sep\nhere." in a)
check("invisibles gone", not any(x in a for x in "\u200b\u00ad\x1b\ufeff\ue000\ufffd\u3164\u2800\ufe0f\U000e0101\u200d"))
check("middle dot -> hyphen", " - dot." in a)
check("em dash collapse -> ' - '", "dash - here." in a)
check("ignored emoji kept", "\U0001f44b" in a)
check("undecided kept", "\u2192" in a and "\u2019" in a and "\ufb01" in a)
check("accented name byte-identical", "Luk\u00e1\u0161 Czerner" in a)
check("tab preserved", "\ttabbed stays." in a)
before = f.read_bytes(); cbefore = Path(c).read_bytes()
run("--config", c, "--fix", "-q", str(f))
check("idempotent: file unchanged on 2nd --fix", f.read_bytes() == before)
check("idempotent: config not re-appended", Path(c).read_bytes() == cbefore)

print("\n== S. layered ledgers: local overrides global per character ==")
home = ROOT / "fakehome"; (home / "charck").mkdir(parents=True)
glob = home / "charck" / "charck.toml"
glob.write_text('[chars."U+2014"]\naction="replace"\nto=" - "\ncollapse=true\n'
                '[chars."U+2192"]\naction="replace"\nto="->"\n'
                '[chars."U+00B7"]\naction="replace"\nto="-"\n', encoding="utf-8")
env = dict(os.environ, XDG_CONFIG_HOME=str(home))
proj = ROOT / "proj" / "deep" / "nested"; proj.mkdir(parents=True)
(ROOT / "proj" / ".charck.toml").write_text(
    '[chars."U+2192"]\naction="replace"\nto="-"\n', encoding="utf-8")
(proj / "doc.md").write_text("a — b, x → y, p · q\n", encoding="utf-8")

def run_in(cwd, *a):
    return subprocess.run([sys.executable, TOOL, *a], capture_output=True,
                          text=True, cwd=str(cwd), env=env)

r = run_in(proj, "--fix", "-q", "doc.md")
got = (proj / "doc.md").read_text(encoding="utf-8")
check("global decision applies (U+2014)", "a - b" in got, repr(got))
check("local overrides global for its character (U+2192)", "x - y" in got, repr(got))
check("unmentioned characters fall through to global (U+00B7)", "p - q" in got, repr(got))

r = run_in(proj, "--list")
check("--list marks which layer decided each character",
      "local" in r.stdout and "global" in r.stdout, r.stdout[-300:])
check("--list shows U+2192 as local",
      any("U+2192" in l and "local" in l for l in r.stdout.splitlines()), r.stdout[-300:])
check("--list shows U+2014 as global",
      any("U+2014" in l and "global" in l for l in r.stdout.splitlines()), r.stdout[-300:])

# a nested cwd must still find the project ledger by walking up
(proj / "d2.md").write_text("x → y\n", encoding="utf-8")
run_in(proj, "--fix", "-q", "d2.md")
check("local ledger found by walking up from a nested cwd",
      (proj / "d2.md").read_text(encoding="utf-8") == "x - y\n")

# new characters land in the local ledger, not the global one
gbefore = glob.read_bytes()
(proj / "new.md").write_text("odd … char\n", encoding="utf-8")
run_in(proj, "-q", "new.md")
check("new characters appended to the local ledger",
      "U+2026" in (ROOT / "proj" / ".charck.toml").read_text(encoding="utf-8"))
check("global ledger untouched when a local one exists", glob.read_bytes() == gbefore)

# the global ledger is NEVER written to; with no local one in scope, a local
# ledger is created in the working directory instead
solo = ROOT / "solo"; solo.mkdir()
(solo / "x.md").write_text("„ quote\n", encoding="utf-8")
gbefore2 = glob.read_bytes()
r = run_in(solo, "-q", "x.md")
check("global ledger never appended to, even with no local one",
      glob.read_bytes() == gbefore2, glob.read_text(encoding="utf-8")[-160:])
check("a local ledger is created in the working directory instead",
      (solo / ".charck.toml").is_file(), sorted(p.name for p in solo.iterdir()))
check("the new character landed there",
      "U+201E" in (solo / ".charck.toml").read_text(encoding="utf-8"))
check("creating a ledger is announced, not silent",
      "created" in r.stdout and ".charck.toml" in r.stdout, r.stdout[-200:])

# a second run in the same place reuses it rather than announcing again
r = run_in(solo, "-q", "x.md")
check("existing ledger is reused without re-announcing", "created" not in r.stdout,
      r.stdout[-160:])

# both ledgers are protected from being rewritten
(ROOT / "proj" / "guard.md").write_text("x → y\n", encoding="utf-8")
lbefore = (ROOT / "proj" / ".charck.toml").read_bytes()
run_in(ROOT / "proj", "--fix", "-q", ".")
check("local ledger not rewritten by a --fix over its own tree",
      (ROOT / "proj" / ".charck.toml").read_bytes() == lbefore)

print("\n== R. the undecided count reflects every undecided character ==")
# Regressions here read as "1 character needs a decision" no matter how many
# there really are, if the set comprehension collects a stale loop variable.
c = cfg("count.toml", "")
f = src("count.md", "em — dash, ’ quote, · dot, → arrow\n")
out = run("--config", c, "-q", str(f)).stdout
check("counts all 4 undecided characters", "4 characters need a decision" in out,
      [l for l in out.splitlines() if "decision" in l])
f2 = src("count2.md", "just — one\n")
out = run("--config", cfg("count2.toml", ""), "-q", str(f2)).stdout
check("singular phrasing for exactly one", "1 character needs a decision" in out,
      [l for l in out.splitlines() if "decision" in l])

print("\n== Q. concurrent appends do not brick the ledger ==")
conc = ROOT / "conc"; conc.mkdir()
for i in range(6):
    src("conc/f%d.md" % i, "a — b \u2026 c \u00b7 d\n")
c = cfg("conc.toml", "")
procs = [subprocess.Popen([sys.executable, TOOL, "--config", c, "-q", str(conc)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(4)]
for p in procs: p.wait()
r = run("--config", c, "--list")
check("config still parses after 4 concurrent runs", r.returncode == 0, r.stderr[-200:])
body = Path(c).read_text(encoding="utf-8")
check("no duplicate tables", body.count('[chars."U+2014"]') == 1,
      "count=%d" % body.count('[chars."U+2014"]'))
check("no duplicate header", body.count("decision ledger") == 1)

print("\n== T. the Makefile lists what it has, and installs nothing system-wide ==")
# The only recipe really run here is `help`, which is echo and awk. The rest are
# dry runs, which is safe for the recipes this Makefile has: `make -n` does still
# execute a line prefixed with `+`, and there is none. Nothing in this section
# installs, uninstalls or deletes anything, and none of it invokes `make test`,
# which would recurse straight back into this file.
REPO = Path(__file__).resolve().parent.parent
MAKEFILE = REPO / "Makefile"
if shutil.which("make") is None or not MAKEFILE.exists():
    # Skip rather than fail: charck itself is one stdlib module, and a checkout
    # without make (or the Makefile) is still a working checkout.
    print("  SKIP  no make, or no Makefile")
else:
    def mk(*a):
        # Drop the parent make's flags: run under `make test -j2` the sub-make
        # would otherwise warn about the unavailable jobserver on stderr.
        env = dict(os.environ)
        for k in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS"):
            env.pop(k, None)
        # DEVNULL and a timeout, because a make that leaves MAKEFILE_LIST unset
        # hands awk no file to read and it would then sit on our stdin forever.
        return subprocess.run(["make", *a], cwd=str(REPO), env=env,
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def target_of(line):
        head = line.split(":", 1)[0]
        if not line[:1].islower() or ":" not in line or head != head.strip():
            return None
        return head if "=" not in head and " " not in head else None

    mkbody = MAKEFILE.read_text(encoding="utf-8")
    phony = [t for line in mkbody.splitlines() if line.startswith(".PHONY:")
             for t in line.split(":", 1)[1].split()]
    # Read the rules themselves rather than trusting .PHONY. Deriving the list
    # from .PHONY alone would leave a target that was never added to it checked
    # by nothing at all, which is the mistake most likely to happen here.
    targets = [t for t in map(target_of, mkbody.splitlines()) if t]
    check("every rule is declared .PHONY", targets and set(targets) == set(phony),
          "rules=%s phony=%s" % (sorted(targets), sorted(phony)))

    bad = [t for t in targets if mk("-n", t).returncode != 0]
    check("every target expands without error", not bad, bad)

    r = mk("help")
    # `-n` proves a recipe expands, never that it runs. help is the default goal
    # and the one target worth proving actually works.
    check("help itself runs", r.returncode == 0, r.stderr[-200:])

    missing = [t for t in targets if ("  %s " % t) not in r.stdout]
    # The failure this pins down: adding a target and forgetting its `##`
    # comment leaves it working but invisible in `make help`.
    check("help lists every target", not missing, missing)

    bare = mk()
    check("bare make prints the help",
          bare.returncode == 0 and bare.stdout == r.stdout, bare.stderr[-200:])

    # The point of installing through pipx is that it stays under $HOME. Recipe
    # lines only: the header comment says "No sudo", and matching prose here
    # would fail on the very sentence that promises the property.
    recipes = "\n".join(l for l in mkbody.splitlines() if l.startswith("\t"))
    check("no recipe escalates or installs system-wide",
          "sudo" not in recipes and "pip install" not in recipes,
          [l for l in recipes.splitlines() if "sudo" in l or "pip install" in l])

print("\n== U. the walk obeys the ledger's ignore patterns ==")
# --no-gitignore throughout: this section is about the ledger, and a .gitignore
# somewhere above TMPDIR would otherwise decide part of the outcome.
DASH = "a — b\n"
U = ROOT / "ig"
for name in ("build", "vendor", ".github", "node_modules", "deep/a/b", "sub"):
    (U / name).mkdir(parents=True)
for name in ("keep.md", "app.js", "app.min.js", "foo", "build/x.md",
             "vendor/lib.md", "vendor/keep.md", ".github/wf.md",
             "node_modules/dep.md", "deep/a/b/c.md", "sub/one.md"):
    (U / name).write_text(DASH, encoding="utf-8")
# The ledger sits in the tree it describes, which is what anchoring is relative
# to: a pattern with a slash means "under this file's own directory".
uc = U / "u.toml"
# The !vendor/keep.md is here to be defeated: vendor/ is pruned, so the walk
# never reaches the file the re-include names.
uc.write_text('[files]\nignore = ["build/", "*.min.js", "vendor/", '
              '"!vendor/keep.md", "!.github/", "foo/"]\n', encoding="utf-8")

def walked(*a):
    return run("--config", str(uc), "--no-append", "--no-gitignore", *a).stdout

out = walked(str(U))
check("an ignored directory is pruned", "build/x.md" not in out, out[:400])
check("a file pattern skips that file and not its neighbour",
      "app.min.js" not in out and "app.js" in out, out[:400])
check("a built-in default still applies", "node_modules/dep.md" not in out, out[:400])
check("! re-includes what a built-in default hides", ".github/wf.md" in out, out[:400])
check("a directory-only pattern does not match a file of that name",
      "%s/foo\n" % U in out, out[:400])
check("everything unmatched is still scanned",
      "keep.md" in out and "sub/one.md" in out and "deep/a/b/c.md" in out, out[:400])
# build, vendor, node_modules, app.min.js. A pruned directory counts once and
# its contents are never enumerated, which is the point of pruning.
check("the summary counts what was ignored", "4 ignored" in out,
      [l for l in out.splitlines() if "scanned" in l])

out = run("--config", str(uc), "--no-append", "--no-gitignore", "-v", str(U)).stdout
check("-v names the pattern that ignored a path and the file it came from",
      "[vendor/ from %s]" % uc in out and "[node_modules/ from built-in defaults]" in out,
      [l for l in out.splitlines() if "ignored" in l])

check("a re-include cannot reach under a pruned directory",
      "vendor/keep.md" not in walked(str(U)), "")
# ...but the same re-include works when the parent is filtered rather than
# pruned, which is what makes the check above about pruning and not about `!`.
loose = U / "loose.toml"
loose.write_text('[files]\nignore = ["vendor/*", "!vendor/keep.md"]\n',
                 encoding="utf-8")
out = run("--config", str(loose), "--no-append", "--no-gitignore", str(U)).stdout
check("a re-include does reach under a directory that was only filtered",
      "vendor/keep.md" in out and "vendor/lib.md" not in out, out[:400])
check("a directory named directly is scanned though a pattern matches it",
      "vendor/lib.md" in walked(str(U / "vendor")), "")
check("a file named directly is scanned though a pattern matches it",
      "app.min.js" in walked(str(U / "app.min.js")), "")

out = run("--config", str(uc), "--no-append", "--no-ignore", str(U)).stdout
check("--no-ignore reaches an ignored tree", "node_modules/dep.md" in out, out[:300])
check("--no-ignore reports nothing as ignored", "0 ignored" in out,
      [l for l in out.splitlines() if "scanned" in l])

fixc = ROOT / "ig" / "fix.toml"
fixc.write_text('[files]\nignore = ["vendor/", "*.min.js"]\n'
                '[chars."U+2014"]\naction = "replace"\nto = "-"\n', encoding="utf-8")
run("--config", str(fixc), "--no-append", "--no-gitignore", "--fix", "-q", str(U))
check("--fix leaves an ignored file byte-identical",
      (U / "vendor" / "lib.md").read_text(encoding="utf-8") == DASH,
      repr((U / "vendor" / "lib.md").read_text(encoding="utf-8")))
check("--fix does rewrite what was scanned",
      (U / "keep.md").read_text(encoding="utf-8") == "a - b\n",
      repr((U / "keep.md").read_text(encoding="utf-8")))

print("\n== U2. glob syntax: what a * may and may not cross ==")
G = ROOT / "globs"
(G / "a" / "b").mkdir(parents=True)
for name in ("x.md", "a/x.md", "a/y.md", "a/b/z.md"):
    (G / name).write_text(DASH, encoding="utf-8")
gc = G / "g.toml"

def globbed(pattern):
    gc.write_text('[files]\nignore = ["%s"]\n' % pattern, encoding="utf-8")
    return run("--config", str(gc), "--no-append", "--no-gitignore", str(G)).stdout

out = globbed("a/*.md")
check("* does not cross a slash",
      "a/y.md" not in out and "a/b/z.md" in out, out[:400])
out = globbed("a/**/z.md")
check("** crosses a slash", "a/b/z.md" not in out and "a/y.md" in out, out[:400])
out = globbed("x.md")
check("a bare name matches at any depth",
      "a/x.md" not in out and "%s/x.md" % G not in out, out[:400])
out = globbed("/x.md")
check("a leading slash anchors to the ledger's own directory",
      "a/x.md" in out and "%s/x.md\n" % G not in out, out[:400])
out = globbed("[xy].md")
check("a character class matches within one segment",
      "a/x.md" not in out and "a/y.md" not in out and "a/b/z.md" in out, out[:400])
out = globbed("**/z.md")
check("a leading **/ is the any-depth form", "a/b/z.md" not in out, out[:400])

print("\n== V. .gitignore is honoured, and only inside a work tree ==")
V = ROOT / "repo"
(V / "sub" / "deeper").mkdir(parents=True)
(V / ".git" / "info").mkdir(parents=True)
for name in ("top.md", "skip.md", "sub/keep.md", "sub/hide.md", "sub/deeper/x.md"):
    (V / name).write_text(DASH, encoding="utf-8")
(V / ".gitignore").write_text("skip.md\nhide.md\n", encoding="utf-8")
vc = cfg("v.toml", "")

def repo_out(*a):
    return run("--config", vc, "--no-append", *a).stdout

out = repo_out(str(V))
check("a repo-root .gitignore is applied",
      "skip.md" not in out and "sub/hide.md" not in out, out[:400])
check("...and everything else is scanned",
      "top.md" in out and "sub/keep.md" in out and "deeper/x.md" in out, out[:400])
check("a .gitignore above the walked directory still applies",
      "sub/hide.md" not in repo_out(str(V / "sub")), "")
check("--no-gitignore stops reading them", "skip.md" in repo_out("--no-gitignore", str(V)),
      "")

(V / "sub" / ".gitignore").write_text("!hide.md\nkeep.md\n", encoding="utf-8")
out = repo_out(str(V))
check("a deeper .gitignore overrides a shallower one",
      "sub/hide.md" in out and "sub/keep.md" not in out, out[:400])

(V / ".git" / "info" / "exclude").write_text("top.md\n", encoding="utf-8")
check(".git/info/exclude is read", "top.md" not in repo_out(str(V)), "")

vc2 = cfg("v2.toml", '[files]\nignore = ["!skip.md"]\n')
check("a ledger pattern has the last word over .gitignore",
      "skip.md" in run("--config", vc2, "--no-append", str(V)).stdout, "")

W = ROOT / "wtree"; W.mkdir()
# A worktree and a submodule have .git as a file, not a directory. The em dash
# in it is what makes the check below mean something: without a reportable
# character, silence would prove nothing.
(W / ".git").write_text("gitdir: /nowhere — x\n", encoding="utf-8")
(W / ".gitignore").write_text("hide.md\n", encoding="utf-8")
for name in ("hide.md", "keep.md"):
    (W / name).write_text(DASH, encoding="utf-8")
out = repo_out(str(W))
check("a .git file marks a work tree just as a .git directory does",
      "hide.md" not in out and "keep.md" in out, out[:400])
r = run("--config", vc, "--no-append", "--no-ignore", "-v", str(W))
check("a .git file is never scanned, even under --no-ignore",
      "gitdir" not in r.stdout, r.stdout[:400])
control = ROOT / "wcontrol"; control.mkdir()
(control / "notgit").write_text("gitdir: /nowhere — x\n", encoding="utf-8")
check("...and the same bytes under any other name are reported",
      "gitdir" in run("--config", vc, "--no-append", "--no-ignore",
                      str(control)).stdout, "")

G = ROOT / "gitesc"; (G / "repo" / ".git").mkdir(parents=True)
(G / "repo" / ".git" / "HEAD").write_text(DASH, encoding="utf-8")
(G / "repo" / "notes.md").symlink_to(Path(".git") / "HEAD")
gc2 = cfg("gitesc.toml", '[chars."U+2014"]\naction = "replace"\nto = "-"\n')
r = run("--config", gc2, "--no-append", "--fix", "-q", str(G))
check("a symlink leading back into .git is not followed by a walk",
      (G / "repo" / ".git" / "HEAD").read_text(encoding="utf-8") == DASH,
      repr((G / "repo" / ".git" / "HEAD").read_text(encoding="utf-8")))
(G / "dotgit").symlink_to(G / "repo" / ".git")     # a name that hides it
(G / "alias").symlink_to(G / "repo")               # .git under a linked parent
for label, arg in [("named directly", G / "repo" / ".git"),
                   ("through a symlink to it", G / "dotgit"),
                   ("through a symlinked parent", G / "alias" / ".git")]:
    r = run("--config", gc2, "--no-append", "--fix", "-q", str(arg))
    check("walking .git %s is refused with exit 2" % label, r.returncode == 2,
          "exit=%d %s" % (r.returncode, r.stderr[-120:]))
check("...and nothing in there was rewritten",
      (G / "repo" / ".git" / "HEAD").read_text(encoding="utf-8") == DASH)
check("a single file inside .git named directly is still scanned",
      "HEAD" in run("--config", vc, "--no-append",
                    str(G / "repo" / ".git" / "HEAD")).stdout, "")

(V / "unreadable.gitignore").write_text("", encoding="utf-8")
NR = ROOT / "norepo"; NR.mkdir()
(NR / ".gitignore").write_text("hide.md\n", encoding="utf-8")
(NR / "hide.md").write_text(DASH, encoding="utf-8")
inner = NR / "inner"; (inner / ".git").mkdir(parents=True)
(inner / ".gitignore").write_text("hide.md\n", encoding="utf-8")
(inner / "hide.md").write_text(DASH, encoding="utf-8")
if any((p / ".git").exists() for p in ROOT.parents):
    # TMPDIR inside somebody's repository: that repo decides, not this test.
    print("  SKIP  TMPDIR is itself inside a work tree")
else:
    out = repo_out(str(NR))
    check("a .gitignore outside any work tree is not read",
          "%s/hide.md" % NR in out, out[:300])
    check("...but a repository below the walk root is still honoured",
          "inner/hide.md" not in out, out[:300])

L = ROOT / "lenient"; (L / ".git").mkdir(parents=True)
# Someone else's file, so one line we cannot compile must not stop the run.
(L / ".gitignore").write_text("../oops\nhide.md\n", encoding="utf-8")
for name in ("hide.md", "ok.md"):
    (L / name).write_text(DASH, encoding="utf-8")
r = run("--config", vc, "--no-append", str(L))
check("an unusable .gitignore line does not stop the run",
      r.returncode == 1 and "ok.md" in r.stdout, "exit=%d %s" % (r.returncode, r.stderr[-160:]))
check("...and the lines around it still apply", "hide.md" not in r.stdout, r.stdout[:300])
check("...and -v says which line could not be used",
      "unusable pattern" in run("--config", vc, "--no-append", "-v", str(L)).stdout, "")

print("\n== W. an unusable pattern in our own ledger is exit 2, not a no-op ==")
for label, body in [
        ("empty pattern", '[files]\nignore = [""]\n'),
        ("lone !", '[files]\nignore = ["!"]\n'),
        ("trailing backslash", '[files]\nignore = ["foo\\\\"]\n'),
        (".. segment", '[files]\nignore = ["../x"]\n'),
        ("empty path segment", '[files]\nignore = ["a//b"]\n'),
        ("uncompilable character class", '[files]\nignore = ["[z-a].md"]\n'),
        ("unknown POSIX class", '[files]\nignore = ["[[:bogus:]].md"]\n'),
        ("ignore not an array", '[files]\nignore = "build/"\n'),
        ("non-string element", '[files]\nignore = [5]\n'),
        ("unknown key in [files]", '[files]\nignores = ["build/"]\n'),
        ("files not a table", "files = 5\n")]:
    r = run("--config", cfg("w.toml", body), "--no-append", str(src("w.md", "a\n")))
    check("%s -> exit 2" % label, r.returncode == 2,
          "exit=%d %s" % (r.returncode, r.stderr[-120:]))

whome = ROOT / "whome"; (whome / "charck").mkdir(parents=True)
wenv = dict(os.environ, XDG_CONFIG_HOME=str(whome))
wdir = ROOT / "wdir"; wdir.mkdir()
for name in ("app.js", "app.min.js"):
    (wdir / name).write_text(DASH, encoding="utf-8")

def in_whome(body, *a):
    (whome / "charck" / "charck.toml").write_text(body, encoding="utf-8")
    return subprocess.run([sys.executable, TOOL, "--no-append", "--no-gitignore", *a],
                          capture_output=True, text=True, cwd=str(wdir), env=wenv)

r = in_whome('[files]\nignore = ["src/x.md"]\n', ".")
check("an anchored pattern in the global ledger -> exit 2", r.returncode == 2,
      "exit=%d %s" % (r.returncode, r.stderr[-160:]))
check("...and the error says where the pattern belongs",
      ".charck.toml" in r.stderr, r.stderr[-160:])
r = in_whome('[files]\nignore = ["*.min.js"]\n', ".")
check("an unanchored pattern in the global ledger applies everywhere",
      "app.min.js" not in r.stdout and "app.js" in r.stdout, r.stdout[:300])

print("\n== X. the corners where gitignore and Python regex disagree ==")
# Every case here was a real divergence from `git ls-files --others
# --exclude-standard`, and each one hid files from the scan rather than showing
# too many, which is the direction that makes a build gate lie.
X = ROOT / "corners"; (X / "d1" / "x").mkdir(parents=True)
# Single-letter basenames, so a class matches the name and not just part of it,
# plus one longer name that every class here must leave alone.
for name in ("w.md", "a.md", "long.md", "d1/b.md", "d1/w.md", "d1/x/b.md"):
    (X / name).write_text(DASH, encoding="utf-8")
xc = X / "x.toml"

def cornered(pattern, *a):
    xc.write_text('[files]\nignore = ["%s"]\n' % pattern, encoding="utf-8")
    return run("--config", str(xc), "--no-append", "--no-gitignore", *a, str(X))

out = cornered("[\\\\w].md").stdout
check("a backslash in a character class is a literal, not a Python class",
      "a.md" in out and "d1/b.md" in out and "w.md" not in out, out[:400])
out = cornered("d1[/]a").stdout
check("a / inside a character class never matches a separator",
      "d1/b.md" in out and "a.md" in out, out[:400])
out = cornered("d1/**b.md").stdout
check("** is a plain * where it is not a whole path segment",
      "d1/x/b.md" in out and "d1/b.md" not in out, out[:400])
r = cornered("[[:alpha:]].md")
check("a POSIX class is spelled out, not passed through",
      "w.md" not in r.stdout and "long.md" in r.stdout, r.stdout[:400])
check("...and Python's regex internals never reach stderr",
      "FutureWarning" not in r.stderr, r.stderr[-200:])
out = cornered("[!w].md").stdout
check("a negated class does not match a separator either",
      "w.md" in out and "a.md" not in out, out[:400])

# A newline is a legal character in a path segment, and `.` does not match one.
# The tool that hunts invisible characters does not get to be defeated by one in
# a directory name.
nl = X / "d1" / "od\nnl"; nl.mkdir()
(nl / "b.md").write_text(DASH, encoding="utf-8")
out = cornered("b.md").stdout
check("an unanchored pattern matches under a directory named with a newline",
      "od\nnl/b.md" not in out and "long.md" in out, out[:400])
out = cornered("d1/**").stdout
check("a trailing ** reaches past one too", "od\nnl/b.md" not in out
      and "d1/b.md" not in out and "a.md" in out, out[:400])

# 11 stacked globstars took 23 seconds before they were collapsed at compile
# time; the walk pays it per path.
deep = X / "deep"
here = deep
for i in range(22):
    here = here / ("l%d" % i)
here.mkdir(parents=True)
(here / "zzz.md").write_text(DASH, encoding="utf-8")
xc.write_text('[files]\nignore = ["%sx.md"]\n' % ("**/" * 11), encoding="utf-8")
try:
    r = subprocess.run([sys.executable, TOOL, "--config", str(xc), "--no-append",
                        "--no-gitignore", "-q", str(X)],
                       capture_output=True, text=True, timeout=20)
    check("stacked globstars do not blow up the walk", True)
except subprocess.TimeoutExpired:
    check("stacked globstars do not blow up the walk", False, "timed out")

print("\n== Y. a .gitignore is read the way git reads it ==")
Y = ROOT / "readgi"; (Y / ".git").mkdir(parents=True)
for name in ("a.md", "b.md"):
    (Y / name).write_text(DASH, encoding="utf-8")
(Y / "a ").write_text(DASH, encoding="utf-8")       # trailing space in the name
yc = cfg("y.toml", "")

def gitignored(body, *a):
    (Y / ".gitignore").write_bytes(body)
    return run("--config", yc, "--no-append", *a, str(Y)).stdout

# The tool exists to find invisible characters; one in a .gitignore used to
# make its first pattern silently match nothing.
out = gitignored(b"\xef\xbb\xbfa.md\n")
check("a BOM does not disarm the first pattern",
      "a.md" not in out and "b.md" in out, out[:400])
out = gitignored(b"a.md\r\nb.md\r\n")
check("CRLF line endings are handled", "a.md" not in out and "b.md" not in out,
      out[:400])
out = gitignored(b"a\\ \n")
check("an escaped trailing space is kept",
      "%s/a \n" % Y not in out and "a.md" in out, out[:400])
out = gitignored(b"a.md   \n")
check("unescaped trailing spaces are dropped", "a.md" not in out, out[:400])
out = gitignored(b"#a.md\n\n   \nb.md\n")
check("comments and blank lines are skipped",
      "a.md" in out and "b.md" not in out, out[:400])

(Y / ".gitignore").write_bytes(b"a.md\n")
os.chmod(Y / ".gitignore", 0o000)
try:
    r = run("--config", yc, "--no-append", "-v", str(Y))
    check("an unreadable .gitignore is reported rather than passed over",
          "unreadable" in r.stdout, [l for l in r.stdout.splitlines()
                                     if "unreadable" in l or "scanned" in l])
    check("...and the run still finishes normally", r.returncode == 1,
          "exit=%d" % r.returncode)
finally:
    os.chmod(Y / ".gitignore", 0o644)

print("\n== Z. --exclude: what a first run leaves out, and what it writes down ==")
# The flag exists for the run before there is a ledger to record anything in,
# so this section uses ledger discovery rather than --config: an empty
# XDG_CONFIG_HOME, so no global ledger is in play, and a tree with none above
# it. --no-gitignore throughout, as a .gitignore above TMPDIR would otherwise
# decide part of the outcome.
zhome = ROOT / "zhome"; zhome.mkdir()
zenv = dict(os.environ, XDG_CONFIG_HOME=str(zhome))
Z = ROOT / "excl"
for name in ("build", "sub/deep", "keep", "vendor", ".github"):
    (Z / name).mkdir(parents=True)
for name in ("x.md", "build/b.md", "sub/s.md", "sub/deep/d.md", "keep/k.md",
             "vendor/v.md", ".github/wf.md"):
    (Z / name).write_text(DASH, encoding="utf-8")
zledger = Z / ".charck.toml"
other = ROOT / "other"; other.mkdir()

def zrun(cwd, *a):
    return subprocess.run([sys.executable, TOOL, "--no-gitignore", *a],
                          capture_output=True, text=True, cwd=str(cwd), env=zenv)

r = zrun(Z, "--exclude", "build/", ".")
check("a first run excludes with no ledger to say so",
      "b.md" not in r.stdout and "x.md" in r.stdout, r.stdout[:400])
led = zledger.read_text(encoding="utf-8")
check("...and the pattern lands in the ledger that run created",
      "[files]" in led and '"build/"' in led, led[-200:])
check("...and recording it is announced, not silent",
      "recorded 1 ignore pattern" in r.stdout, r.stdout[-300:])
r = zrun(Z, ".")
check("the next run needs no flag", "b.md" not in r.stdout and "x.md" in r.stdout,
      r.stdout[:400])

before = zledger.read_bytes()
r = zrun(Z, "--exclude", "keep/", ".")
after = zledger.read_text(encoding="utf-8")
check("a second pattern goes into the array that is already there",
      after.count("\nignore = [") == 1 and '"keep/"' in after, after[-200:])
# The one place a ledger is edited rather than grown, so this asserts on the
# bytes: everything but the inserted line has to be exactly what it was.
check("...and not a byte of the rest of the ledger moves",
      after.replace('  "keep/",\n', "", 1).encode("utf-8") == before,
      repr(after[-200:]))
check("...and it takes effect on the run that recorded it",
      "k.md" not in r.stdout, r.stdout[:400])
r = zrun(Z, "--exclude", "keep/", ".")
check("a pattern already in the ledger is not recorded twice",
      zledger.read_text(encoding="utf-8").count('"keep/"') == 1
      and "recorded" not in r.stdout, r.stdout[-200:])

# Anchoring is relative to the file a pattern lives in, and the ledger is not
# always the directory you are standing in. Both meanings have to survive.
r = zrun(Z / "sub", "--exclude", "/deep/", ".")
led = zledger.read_text(encoding="utf-8")
check("an anchored --exclude excludes relative to the working directory",
      "d.md" not in r.stdout and "s.md" in r.stdout, r.stdout[:400])
check("...and is recorded relative to the ledger instead",
      '"/sub/deep/"' in led and '"/deep/"' not in led, led[-200:])
check("...visibly, not behind your back",
      'recorded as "/sub/deep/"' in r.stdout, r.stdout[-300:])
r = zrun(Z, ".")
check("the recorded form means the same place from the ledger's own directory",
      "d.md" not in r.stdout and "s.md" in r.stdout, r.stdout[:400])

# Shapes a hand-edited array comes in. A comment belongs to the entry its
# author wrote it after, so the new entry goes below it, not between the two.
zc = cfg("shape.toml", '[files]\nignore = [\n  "vendor/",   # PDF pastes\n]\n')
zrun(Z, "--config", zc, "--exclude", "keep/", "-q", ".")
got = Path(zc).read_text(encoding="utf-8")
# The undecided U+2014 is appended to the same ledger by the same run, so the
# assertion is on the array and what surrounds it, not on the whole file.
check("a comment stays with the entry it was written after",
      got.startswith('[files]\nignore = [\n  "vendor/",   # PDF pastes\n'
                     '  "keep/",\n]\n'), repr(got[:120]))
zs = cfg("oneline.toml", '[files]\nignore = ["vendor/"]\n')
zrun(Z, "--config", zs, "--exclude", "keep/", "-q", ".")
got = Path(zs).read_text(encoding="utf-8")
check("a single-line array stays on one line",
      got.startswith('[files]\nignore = ["vendor/", "keep/"]\n'), repr(got[:120]))

# files.ignore as a dotted key is valid TOML this cannot locate for certain.
# Guessing at someone's ledger is not on, so it is left alone and said so.
zd = cfg("dotted.toml", 'files.ignore = ["vendor/"]\n')
r = zrun(Z, "--config", zd, "--exclude", "keep/", "-q", ".")
got = Path(zd).read_text(encoding="utf-8")
check("a ledger whose ignore array cannot be found is left alone",
      got.startswith('files.ignore = ["vendor/"]\n') and "keep/" not in got,
      repr(got[:120]))
check("...and the pattern to add by hand is printed",
      "could not record" in r.stdout and '"keep/"' in r.stdout, r.stdout[-300:])

zn = cfg("noapp.toml", '[files]\nignore = []\n')
before = Path(zn).read_bytes()
r = zrun(Z, "--config", zn, "--no-append", "--exclude", "keep/", ".")
check("--no-append applies the pattern to this run",
      "k.md" not in r.stdout and "v.md" in r.stdout, r.stdout[:400])
check("...and records nothing", Path(zn).read_bytes() == before)
# --no-ignore turns off what was recorded; a pattern given on the same command
# line is what is being asked for right now.
r = zrun(Z, "--config", zn, "--no-append", "--no-ignore", "--exclude", "keep/", ".")
check("--exclude survives --no-ignore",
      "k.md" not in r.stdout and "v.md" in r.stdout, r.stdout[:400])
r = zrun(Z, "--config", zn, "--no-append", "--exclude", "keep/", "keep/k.md")
check("a path named directly is scanned though --exclude names it",
      "k.md" in r.stdout, r.stdout[:400])

zg = cfg("bang.toml", '[files]\nignore = ["!.github/"]\n')
r = zrun(Z, "--config", zg, "--no-append", ".")
check("a recorded ! brings a built-in default back", "wf.md" in r.stdout,
      r.stdout[:400])
r = zrun(Z, "--config", zg, "--no-append", "--exclude", ".github/", ".")
check("--exclude outranks a recorded re-include", "wf.md" not in r.stdout,
      r.stdout[:400])

# Ours, so exit 2, exactly as an unusable pattern in a ledger is.
before = Path(zn).read_bytes()
r = zrun(Z, "--config", zn, "--exclude", "a//b", "-q", ".")
check("an unusable --exclude is exit 2 and stops the run",
      r.returncode == 2 and "--exclude" in r.stderr and "empty path segment"
      in r.stderr, r.stderr[:200])
check("...with the ledger untouched", Path(zn).read_bytes() == before)

# An anchored pattern that could not be written down without changing meaning
# is refused before the walk, not applied and then quietly dropped.
zf = other / "f.toml"; zf.write_text("", encoding="utf-8")
r = zrun(Z, "--config", str(zf), "--exclude", "sub/deep/", "-q", ".")
check("an anchored --exclude the ledger cannot hold stops the run",
      r.returncode == 2 and "anchored" in r.stderr, r.stderr[:300])
r = zrun(Z, "--config", str(zf), "--no-append", "--exclude", "sub/deep/", ".")
check("...and --no-append is the way through",
      r.returncode == 1 and "d.md" not in r.stdout, r.stdout[:400])

# The rules match at any depth, unlike a ledger's, whose group only applies
# under its own directory. A prefix here would make this silently scan keep/.
r = zrun(other, "--config", zn, "--no-append", "--exclude", "keep/", str(Z))
check("an unanchored --exclude reaches a tree outside the working directory",
      "k.md" not in r.stdout and "v.md" in r.stdout, r.stdout[:400])

r = zrun(Z, "--config", zn, "--no-append", "-v", "--exclude", "keep/", ".")
check("-v names --exclude as the source of the rule",
      "[keep/ from --exclude]" in r.stdout,
      [l for l in r.stdout.splitlines() if "ignored" in l])
r = zrun(Z, "--config", zn, "--list", "--exclude", "keep/")
check("--list shows a command-line pattern as cli",
      any(l.startswith("cli") and "keep/" in l for l in r.stdout.splitlines()),
      r.stdout[-300:])

# A ledger's patterns only apply under its own directory, so a pattern recorded
# for a tree somewhere else would work on this run and do nothing on the next.
r = zrun(Z, "--exclude", "keep/", str(other))
check("a walk root the ledger is not above stops the run",
      r.returncode == 2 and "could never apply" in r.stderr, r.stderr[:200])

# Declining to write is not an operational failure, but it does leave something
# to act on, so a build gate must not read it as a clean pass.
(Z / "clean.md").write_text("plain ascii\n", encoding="utf-8")
r = zrun(Z, "--config", zd, "--exclude", "build/", "-q", "clean.md")
check("a pattern that could not be recorded is exit 1 on an otherwise clean run",
      r.returncode == 1 and "could not record" in r.stdout,
      "exit=%d %s" % (r.returncode, r.stdout[-200:]))

# The two rewrites `record_form` performs. Neither shows up in a report, and
# both produce a pattern that means something else when they are missed.
zm = ROOT / "meta"
for name in ("a*b/build", "aXb/build"):
    (zm / name).mkdir(parents=True)
for name in ("a*b/x.md", "a*b/build/b.md", "aXb/build/c.md"):
    (zm / name).write_text(DASH, encoding="utf-8")
(zm / ".charck.toml").write_text('[files]\nignore = []\n', encoding="utf-8")
zrun(zm / "a*b", "-q", "--exclude", "/build/", ".")
led = (zm / ".charck.toml").read_text(encoding="utf-8")
check("a glob character in the path is escaped on the way into the ledger",
      '"/a\\\\*b/build/"' in led, led[:200])
r = zrun(zm, "-v", ".")
hits = [l for l in r.stdout.splitlines() if l.startswith("  ignored")]
check("...so the recorded pattern still names the one directory it meant",
      any("a*b/build" in l for l in hits)
      and not any("aXb/build" in l for l in hits), hits)

zb = ROOT / "bang"; (zb / "sub" / "deep").mkdir(parents=True)
for name in ("sub/x.md", "sub/deep/d.md"):
    (zb / name).write_text(DASH, encoding="utf-8")
(zb / ".charck.toml").write_text('[files]\nignore = ["deep/"]\n', encoding="utf-8")
r = zrun(zb / "sub", "--exclude", "!/deep/", ".")
led = (zb / ".charck.toml").read_text(encoding="utf-8")
check("a leading ! survives the rewrite rather than being buried inside it",
      '"!/sub/deep/"' in led, led[:200])
check("...and the re-include works on the run that recorded it",
      "d.md" in r.stdout, r.stdout[:400])
r = zrun(zb, ".")
check("...and again from the ledger's own directory", "d.md" in r.stdout,
      r.stdout[:400])

# The ledger anchors to the directory holding the file, symlink or not, so the
# rewrite has to use that directory and not the one the link points into.
zl = ROOT / "linked"; (zl / "proj" / "sub").mkdir(parents=True)
(zl / "shared").mkdir()
(zl / "proj" / "sub" / "x.md").write_text(DASH, encoding="utf-8")
(zl / "shared" / "led.toml").write_text('[files]\nignore = []\n', encoding="utf-8")
os.symlink("../shared/led.toml", zl / "proj" / ".charck.toml")
r = zrun(zl / "proj" / "sub", "-q", "--exclude", "/build/", "--exclude",
         "/build/", ".")
check("a symlinked ledger anchors the recorded pattern to the link's directory",
      '"/sub/build/"' in (zl / "shared" / "led.toml").read_text(encoding="utf-8"),
      (zl / "shared" / "led.toml").read_text(encoding="utf-8")[:200])
check("...and the same pattern twice is reported once",
      r.stdout.count("recorded as") == 1, r.stdout[-300:])

# Shapes that used to be recorded wrongly, or not at all.
zt = cfg("nocomma.toml", '[files]\nignore = [\n  "vendor/"   # third-party\n]\n')
zrun(Z, "--config", zt, "--exclude", "keep/", "-q", ".")
got = Path(zt).read_text(encoding="utf-8")
check("a comment on an entry with no comma keeps the entry it describes",
      got.startswith('[files]\nignore = [\n  "vendor/",   # third-party\n'
                     '  "keep/",\n]\n'), repr(got[:120]))
zz = cfg("zeroindent.toml", '[files]\nignore = [\n"a/",\n]\n')
zrun(Z, "--config", zz, "--exclude", "keep/", "-q", ".")
got = Path(zz).read_text(encoding="utf-8")
check("an array written at column zero stays at column zero",
      got.startswith('[files]\nignore = [\n"a/",\n"keep/",\n]\n'), repr(got[:120]))
zr = cfg("crlf.toml", "")
Path(zr).write_bytes(b'[files]\r\nignore = [\r\n  "vendor/",\r\n]\r\n')
zrun(Z, "--config", zr, "--exclude", "keep/", "-q", ".")
check("a CRLF ledger can record a pattern too",
      b'"keep/"' in Path(zr).read_bytes(), Path(zr).read_bytes()[:120])

# The insert is the one edit this makes to a ledger, so a write that cannot
# happen has to leave every decision in it exactly where it was.
zw = ROOT / "nowrite"; zw.mkdir()
(zw / "x.md").write_text(DASH, encoding="utf-8")
zwl = zw / "led.toml"
zwl.write_text('[files]\nignore = ["v/"]\n[chars."U+2014"]\naction = "delete"\n',
               encoding="utf-8")
before = zwl.read_bytes()
os.chmod(zw, 0o555)
try:
    r = zrun(ROOT, "--config", str(zwl), "--exclude", "build/", "-q",
             str(zw / "x.md"))
    check("a ledger that cannot be written is left exactly as it was",
          zwl.read_bytes() == before, zwl.read_text(encoding="utf-8")[:200])
    check("...and that is exit 2 with a message, not a traceback",
          r.returncode == 2 and "Traceback" not in r.stderr, r.stderr[-200:])
finally:
    os.chmod(zw, 0o755)

# This tool of all tools does not get to pretend a path segment cannot hold a
# newline. `.` in the any-depth prefix does not match one; `(?s:.)` does.
znl = ROOT / "od\nnl"; (znl / "build").mkdir(parents=True)
for name in ("s.md", "build/b.md"):
    (znl / name).write_text(DASH, encoding="utf-8")
r = zrun(znl, "--config", zn, "--no-append", "--exclude", "build/", ".")
check("a newline in a directory name does not defeat an unanchored pattern",
      "b.md" not in r.stdout and "s.md" in r.stdout, r.stdout[:400])

print("\n%d passed, %d failed" % (passed, failed))
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(1 if failed else 0)
