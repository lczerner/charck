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
r = run("--config", cfg("b.toml", ""), "-q", str(blob))
check("NUL past 8 KiB is still detected as binary", "binary (NUL byte)" in r.stdout, r.stdout[-160:])

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

print("\n%d passed, %d failed" % (passed, failed))
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(1 if failed else 0)
