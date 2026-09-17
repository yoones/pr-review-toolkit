#!/usr/bin/env python3
"""Build code blocks for a pr-flow spec without hand-copying line numbers.

CLI (each command prints one JSON block on stdout):

  blocks.py diff  <pr.diff> <path> [--hunk N] [--hunk M ...] [--trim A-B]
      Verbatim hunks of <path> from a `gh pr diff` dump. --hunk selects by index
      (default: all). --trim A-B keeps only added lines A..B of a *new-file* hunk and
      recomputes the @@ header.

  blocks.py ctx   <ref> <path> <A-B>[,<C-D>...] [--hl 12,14] [--note "..."]
      Out-of-diff excerpt read from `git show <ref>:<path>`; several ranges become
      segments separated by an elision row. --hl lists the lines to highlight.

  blocks.py facts '<text>' [--cmd '<command>' --out '<output>'] ...
      One fact item; run the command yourself and paste its real output.

Importable too:  from blocks import Blocks; b = Blocks(ref, diff_path); b.ctx(...); b.diff(...)
"""

import argparse
import json
import re
import subprocess
import sys

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class Blocks:
    def __init__(self, ref=None, diff_path=None):
        self.ref = ref
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
            out = subprocess.run(["git", "show", f"{self.ref}:{path}"], capture_output=True, text=True, encoding="utf-8")
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
        item = {"text": text}
        if detail:
            item["detail"] = detail
        if command:
            item["command"] = command
            item["output"] = output or ""
        return item

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
    f.add_argument("text", nargs="+"); f.add_argument("--cmd", dest="command", action="append"); f.add_argument("--out", action="append")
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
        cmds, outs = a.command or [], a.out or []
        items = [Blocks.fact(t, cmds[i] if i < len(cmds) else None, outs[i] if i < len(outs) else None)
                 for i, t in enumerate(a.text)]
        print(json.dumps(Blocks.facts(items), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
