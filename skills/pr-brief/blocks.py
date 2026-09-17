#!/usr/bin/env python3
"""Build code blocks for a pr-brief / pr-flow spec without hand-copying anything.

Shared by both skills: `diff`, `context` and `facts` blocks have the same shape in a brief
and in a flow. Run it from the repository (or the scratchpad clone) that holds the ref.

CLI (each command prints one JSON block on stdout):

  blocks.py diff  <pr.diff> <path> [--hunk N] [--hunk M ...] [--trim A-B]
      Verbatim hunks of <path> from a `gh pr diff` dump. --hunk selects by index
      (default: all). --trim A-B keeps only added lines A..B of a *new-file* hunk and
      recomputes the @@ header.

  blocks.py ctx   <ref> <path> <A-B>[,<C-D>...] [--hl 12,14] [--note "..."]
      Out-of-diff excerpt read from `git show <ref>:<path>`; several ranges become
      segments separated by an elision row. --hl lists the lines to highlight.

  blocks.py facts '<text>' --run '<command>'
      One fact item whose command is RUN here and whose output is captured verbatim.
      Several --run flags pair with several texts, in order.
  blocks.py facts '<text>' [--cmd '<command>' --out '<output>']
      Only for an output that no shell command can reproduce (a console scenario):
      the output is pasted from where it was produced, verbatim.

Importable (the usual way — write a builder script in the scratchpad):

    from blocks import Blocks
    B = Blocks(ref="refs/pr-brief/<N>", diff_path="<scratch>/pr-<N>.diff")
    B.diff(path)                                  # every hunk of the file, verbatim
    B.diff(path, hunks=[0])                       # selected hunks
    B.diff(path, trim=(31, 71))                   # new-file hunk cut to added lines 31..71
    B.ctx(path, [(18, 30)], highlight=[22])       # excerpt from the ref, with line numbers
    B.ran("`Foo` has no spec.", "git grep -n Foo refs/pr-brief/<N> -- spec")   # runs it
    Blocks.facts([...])                           # wraps fact items into a block
"""

import argparse
import json
import re
import subprocess
import sys

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


EMPTY_OUTPUT = "(aucun résultat)"


class Blocks:
    def __init__(self, ref=None, diff_path=None, cwd=None):
        self.ref = ref
        self.cwd = cwd  # repository to run git and fact commands in (default: current dir)
        self._files = {}
        self._hunks = {}
        if diff_path:
            self._parse(open(diff_path, encoding="utf-8").read())

    # ---- diff ----
    def _parse(self, text):
        files, cur = {}, None
        for line in text.split("\n"):
            m = re.match(r"^diff --git a/(.*?) b/(.*)$", line)
            if m:
                cur = m.group(2)
                files[cur] = []
                continue
            if cur is None:
                continue
            if line.startswith("@@"):
                files[cur].append([line])
                continue
            if line.startswith(("+++", "---", "index ", "new file", "deleted file", "similarity", "rename ")):
                continue
            if files[cur]:
                files[cur][-1].append(line)
        self._hunks = {p: ["\n".join(h).rstrip("\n") for h in hs] for p, hs in files.items()}

    def paths(self):
        return list(self._hunks)

    def hunk(self, path, index=0):
        return self._hunks[path][index]

    def trim_new(self, path, start, end, index=0):
        """Keep added lines start..end of a new-file hunk; header recomputed."""
        body = self._hunks[path][index].split("\n")[1:]
        added = [l for l in body if l.startswith("+")]
        kept = added[start - 1:end]
        return "\n".join([f"@@ -0,0 +{start},{len(kept)} @@"] + kept)

    def diff(self, path, hunks=None, trim=None):
        if trim:
            chosen = [self.trim_new(path, trim[0], trim[1], (hunks or [0])[0])]
        elif hunks is None:
            chosen = list(self._hunks[path])
        else:
            chosen = [self._hunks[path][i] for i in hunks]
        return {"kind": "diff", "path": path, "hunks": chosen}

    # ---- context ----
    def show(self, path):
        if path not in self._files:
            out = subprocess.run(["git", "show", f"{self.ref}:{path}"],
                                 capture_output=True, text=True, encoding="utf-8", cwd=self.cwd)
            if out.returncode:
                sys.exit(out.stderr.strip())
            self._files[path] = out.stdout.split("\n")
        return self._files[path]

    def ctx(self, path, ranges, highlight=None, note=None):
        """ranges: [(start, end), ...]; highlight: iterable of absolute line numbers."""
        hl = set(highlight or [])
        segs = []
        for start, end in ranges:
            lines = self.show(path)[start - 1:end]
            seg = {"start_line": start, "code": "\n".join(lines)}
            seg_hl = sorted(n for n in hl if start <= n <= end)
            if seg_hl:
                seg["highlight"] = seg_hl
            segs.append(seg)
        block = {"kind": "context", "path": path}
        if len(segs) == 1:
            block.update(segs[0])
        else:
            block["segments"] = segs
        if note:
            block["note"] = note
        return block

    @staticmethod
    def fact(text, command=None, output=None, detail=None):
        """A fact item with a pasted output. Prefer `ran`, which executes the command."""
        item = {"text": text}
        if detail:
            item["detail"] = detail
        if command:
            item["command"] = command
            item["output"] = output or EMPTY_OUTPUT
        return item

    def ran(self, text, command, detail=None, empty=EMPTY_OUTPUT):
        """A fact item whose command is executed now; its real output is embedded.

        stdout wins; an empty stdout falls back to stderr, then to `empty` (the text the
        page shows for "nothing matched"). The exit code is deliberately ignored: `grep`
        exits 1 on no match, and no match is often the very fact being established.
        """
        run = subprocess.run(command, shell=True, capture_output=True, text=True,
                             encoding="utf-8", cwd=self.cwd)
        output = run.stdout.strip() or run.stderr.strip() or empty
        return self.fact(text, command, output, detail)

    @staticmethod
    def facts(items):
        return {"kind": "facts", "items": list(items)}


def parse_ranges(spec):
    out = []
    for part in spec.split(","):
        a, b = part.split("-") if "-" in part else (part, part)
        out.append((int(a), int(b)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff")
    d.add_argument("diff_path"); d.add_argument("path")
    d.add_argument("--hunk", type=int, action="append")
    d.add_argument("--trim")
    c = sub.add_parser("ctx")
    c.add_argument("ref"); c.add_argument("path"); c.add_argument("ranges")
    c.add_argument("--hl"); c.add_argument("--note")
    f = sub.add_parser("facts")
    f.add_argument("text", nargs="+")
    f.add_argument("--run", action="append", help="command to execute; its output is captured")
    f.add_argument("--cmd", dest="command", action="append", help="command shown but not run (with --out)")
    f.add_argument("--out", action="append")
    a = ap.parse_args()

    if a.cmd == "diff":
        b = Blocks(diff_path=a.diff_path)
        trim = tuple(int(x) for x in a.trim.split("-")) if a.trim else None
        print(json.dumps(b.diff(a.path, a.hunk, trim), ensure_ascii=False, indent=1))
    elif a.cmd == "ctx":
        b = Blocks(ref=a.ref)
        hl = [int(x) for x in a.hl.split(",")] if a.hl else None
        print(json.dumps(b.ctx(a.path, parse_ranges(a.ranges), hl, a.note), ensure_ascii=False, indent=1))
    else:
        b = Blocks()
        runs, cmds, outs = a.run or [], a.command or [], a.out or []
        items = []
        for i, text in enumerate(a.text):
            if i < len(runs):
                items.append(b.ran(text, runs[i]))
            else:
                items.append(Blocks.fact(text, cmds[i] if i < len(cmds) else None,
                                         outs[i] if i < len(outs) else None))
        print(json.dumps(Blocks.facts(items), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
