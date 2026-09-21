#!/usr/bin/env python3
"""Render a PR flow spec (JSON) into a self-contained interactive HTML page.

    python3 render.py flow.json out.html [--open]

--open shows the page in the default browser; works the same on Linux and macOS.
The output directory is created when missing.

The page is a left-to-right SVG diagram (columns of nodes, labelled edges) where every
node opens a modal carrying the code it stands for: diff hunks (verbatim) and out-of-diff
excerpts with highlighted lines. Layout is computed here — the JSON carries content, not
coordinates. See SKILL.md for the JSON shape.
"""

import hashlib
import html
import json
import os
import re
import sys

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")

X0, COL_GAP, Y0 = 20, 50, 44
NODE_GAP, PAD = 10, 10
LINE_H, TITLE_H = 16, 20
TITLE_EXTRA_H = 18          # each wrapped title line after the first
NONE_STUB_W = 90            # stub + ∅ circle of a `none` edge; its label is drawn inside the box
CUE_W = 80                  # room kept free of the title for the cue and the two handles
LABEL_STAGGER = 13          # several labels sharing one anchor are stacked by this much

# Estimated advance width per character, by text class, in px. SVG text cannot wrap by
# itself, so the layout wraps every title and line to the box width from these figures
# (IBM Plex Mono advances 0.6 em; the sans figures are averages with a margin).
CHAR_W = {"t": 8.1, "t sans": 7.4, "code": 7.1, "sub": 6.6, "lab": 6.6, "sans": 6.1, "text": 6.4}


def wrap_text(text, max_chars):
    """Greedy word wrap; a token longer than the width is cut hard. Never returns []."""
    text = "" if text is None else str(text)
    max_chars = max(4, int(max_chars))
    lines, cur = [], ""
    for word in text.split(" "):
        while len(word) > max_chars:
            if cur:
                lines.append(cur); cur = ""
            lines.append(word[:max_chars]); word = word[max_chars:]
        cand = word if not cur else f"{cur} {word}"
        if len(cand) <= max_chars:
            cur = cand
        else:
            lines.append(cur); cur = word
    lines.append(cur)
    return lines or [""]


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def rich(value):
    """Escape, then turn `backticked` spans into inline code (HTML parts only)."""
    return INLINE_CODE_RE.sub(r"<code>\1</code>", esc(value))


def as_list(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


# Default accent tones. `a`/`b`/`c` are the paths a diagram traces; `off` is "nothing
# happens"; `note` is an aside; `band` a full-width strip; `plain` a neutral node.
DEFAULT_TONES = {
    "a": {"light": ("#0e6f8e", "#e3f1f6"), "dark": ("#57b8d6", "#143743")},
    "b": {"light": ("#6f3f9e", "#efe7f7"), "dark": ("#b48ce0", "#2e2240")},
    "c": {"light": ("#a3541a", "#fbeadb"), "dark": ("#e8a06a", "#3e2a1a")},
}


class Flow:
    def __init__(self, data):
        self.d = data
        self.repo = str(data.get("repo", ""))
        self.head = str(data.get("head_sha", ""))
        self.pr_url = str(data.get("url", ""))
        self.tones = {}
        for key, base in DEFAULT_TONES.items():
            self.tones[key] = dict(base)
        for key, spec in (data.get("tones") or {}).items():
            self.tones.setdefault(key, dict(DEFAULT_TONES.get(key, DEFAULT_TONES["a"])))
            self.tones[key].update(spec or {})
        self.columns = data.get("columns") or []
        self.nodes = data.get("nodes") or []
        self.edges = data.get("edges") or []
        self.layout()

    # ---------------------------------------------------------------- layout
    def layout(self):
        ids = [c["id"] for c in self.columns]
        node_col = {n["id"]: n.get("col") for n in self.nodes}
        # Edge labels live in the gap between two columns: widen each gap to its longest label
        # (a horizontal label at the start or the end of the edge) so text never runs over a box.
        gaps = [COL_GAP] * max(0, len(ids) - 1)
        self.none_labels = {}
        for e in self.edges:
            src = node_col.get(e.get("from"))
            if e.get("kind") == "none":
                self.none_labels.setdefault(e["from"], []).append(e.get("label") or "")
                if src in ids and ids.index(src) < len(gaps):
                    gaps[ids.index(src)] = max(gaps[ids.index(src)], NONE_STUB_W)
                continue
            dst = node_col.get(e.get("to"))
            label = e.get("label")
            if not label or src not in ids or dst not in ids or e.get("label_at") == "vertical":
                continue
            i, j = ids.index(src), ids.index(dst)
            if i == j:
                continue
            need = int(len(str(label)) * CHAR_W["lab"]) + 16
            g = (j - 1 if e.get("label_at") == "end" else i) if i < j else (i - 1 if e.get("label_at") != "end" else j)
            g = min(max(g, 0), len(gaps) - 1)
            gaps[g] = max(gaps[g], need)
        x = X0
        self.col_x, self.col_w, self.gap_after = {}, {}, {}
        for index, col in enumerate(self.columns):
            w = int(col.get("width") or 280)
            self.col_x[col["id"]], self.col_w[col["id"]] = x, w
            self.gap_after[col["id"]] = gaps[index] if index < len(gaps) else 0
            x += w + self.gap_after[col["id"]]
        self.total_w = x + X0

        cursor = {c["id"]: Y0 for c in self.columns}
        self.rect, self.text = {}, {}
        for node in self.nodes:
            col = node.get("col")
            if col == "*":
                nx, nw = X0, self.total_w - 2 * X0
            else:
                nx, nw = self.col_x[col], self.col_w[col]
                if node.get("span"):
                    ids = [c["id"] for c in self.columns]
                    last = ids[min(len(ids) - 1, ids.index(col) + int(node["span"]) - 1)]
                    nw = self.col_x[last] + self.col_w[last] - nx
            title_lines, lines = self.wrap_node(node, nw)
            h = int(node.get("height") or max(40, PAD + TITLE_H + TITLE_EXTRA_H * (len(title_lines) - 1)
                                                  + LINE_H * len(lines) + 8))
            if col == "*":
                y = node.get("y")
                if y is None:
                    y = max(cursor.values()) + 30
                y = int(y)
                for key in cursor:
                    cursor[key] = y + h + NODE_GAP
            else:
                y = node.get("y")
                if y is None:
                    y = cursor[col] + int(node.get("gap") or 0)
                y = int(y)
                cursor[col] = y + h + NODE_GAP
            self.rect[node["id"]] = (nx, y, nw, h)
            self.text[node["id"]] = (title_lines, lines)
        self.label_dy = self.stagger_labels()
        bottom = max((r[1] + r[3] for r in self.rect.values()), default=Y0)
        self.legend_y = bottom + 34
        self.total_h = self.legend_y + 14

    def stagger_labels(self):
        """Every edge leaving a box starts at the middle of its right border, so several
        labelled edges from the same box would print their labels on the very same pixel.
        Stack them upwards instead; the gap they sit in is empty."""
        dy, seen = {}, {}
        for index, edge in enumerate(self.edges):
            if not edge.get("label") or edge.get("kind") == "none":
                continue
            src, dst = edge.get("from"), edge.get("to")
            if dst is None or src not in self.rect or dst not in self.rect:
                continue
            ax, ay, aw, ah = self.rect[src]
            bx, by, bw, bh = self.rect[dst]
            if ax + aw <= bx:
                at = edge.get("label_at")
                key = ("end", dst) if at == "end" else (("vert", src, dst) if at == "vertical" else ("start", src))
            elif bx + bw <= ax:
                key = ("rl", src)
            else:
                key = ("col", src, dst)
            rank = seen.get(key, 0)
            seen[key] = rank + 1
            if rank:
                dy[index] = -LABEL_STAGGER * rank
        return dy

    def wrap_node(self, node, width):
        """Wrap the title and every line to the box width. Returns (title_lines, [(cls, text)])."""
        avail = width - 20
        title_key = "t sans" if node.get("sans") else "t"
        title_lines = wrap_text(node.get("title"), (avail - CUE_W) / CHAR_W[title_key])
        tone = node.get("tone") or "plain"
        lines = []
        for line in node.get("lines") or []:
            if isinstance(line, str):
                line = {"text": line}
            style = line.get("style") or "code"
            lcls = {"code": "", "sub": "sub", "lab": f"lab {esc(tone)}" if tone in self.tones else "lab",
                    "sans": "sub sans", "text": "sans"}.get(style, "")
            for piece in wrap_text(line.get("text"), avail / CHAR_W.get(style, CHAR_W["code"])):
                lines.append((lcls, piece))
        for label in self.none_labels.get(node["id"], []):
            for piece in wrap_text("∅ " + label, avail / CHAR_W["sub"]):
                lines.append(("sub off", piece))
        return title_lines, lines

    # ---------------------------------------------------------------- svg
    def svg_node(self, node):
        x, y, w, h = self.rect[node["id"]]
        tone = node.get("tone") or "plain"
        cls = f"box {esc(tone)}" if tone != "plain" else "box"
        has_modal = bool(node.get("blocks")) or bool(node.get("intro"))
        cue = node.get("cue") or ("diff" if any(b.get("kind") == "diff" for b in node.get("blocks") or []) else "code")
        out = [f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}"/>']
        ty = y + PAD + 6
        title_cls = "t sans" if node.get("sans") else "t"
        if tone == "off":
            title_cls += " off"
        title_lines, lines = self.text[node["id"]]
        for index, piece in enumerate(title_lines):
            out.append(f'<text class="{title_cls}" x="{x + 10}" y="{ty}">{esc(piece)}</text>')
            ty += TITLE_EXTRA_H if index < len(title_lines) - 1 else TITLE_H - 2
        for lcls, piece in lines:
            out.append(f'<text class="{lcls}" x="{x + 10}" y="{ty}">{esc(piece)}</text>')
            ty += LINE_H
        if has_modal:
            out.append(f'<text class="cue" x="{x + w - 48}" y="{y + 12}" text-anchor="end">{esc(cue)} ›</text>')
        title_txt = esc(node.get("title") or node["id"])
        ix, dx2, hy = x + w - 31, x + w - 13, y + 11
        out.append(f'<g class="iso" data-iso="{esc(node["id"])}" role="button" tabindex="0" '
                   f'aria-label="Isoler le sous-flux : {title_txt}">'
                   f'<circle class="isob" cx="{ix}" cy="{hy}" r="7.5"/>'
                   f'<circle class="isod" cx="{ix}" cy="{hy}" r="2.6"/></g>')
        out.append(f'<g class="del" data-del="{esc(node["id"])}" role="button" tabindex="0" '
                   f'aria-label="Retirer du schéma : {title_txt}">'
                   f'<circle class="delb" cx="{dx2}" cy="{hy}" r="7.5"/>'
                   f'<path class="delx" d="M{dx2 - 3.4} {hy - 3.4} l6.8 6.8 M{dx2 + 3.4} {hy - 3.4} l-6.8 6.8"/>'
                   f'</g>')
        label = esc(node.get("modal_title") or node.get("title") or node["id"])
        aria = f"Ouvrir le code : {label}" if has_modal else label
        modal = ' data-modal="1"' if has_modal else ""
        return (f'<g class="node" data-node="{esc(node["id"])}" transform="translate(0,0)" '
                f'tabindex="0" role="button" aria-label="{aria}"{modal}>{"".join(out)}</g>')

    def default_elbow(self, edge, sx, ex):
        """Elbow of a left-to-right edge: mid-gap when the columns are adjacent, else in the
        middle of the first gap so the vertical run sits between columns, not across boxes."""
        node_col = {n["id"]: n.get("col") for n in self.nodes}
        src = node_col.get(edge.get("from"))
        if src in self.gap_after and sx + self.gap_after[src] < ex:
            return sx + self.gap_after[src] // 2
        return sx + (ex - sx) // 2

    def svg_edge(self, edge, index=0):
        return (f'<g class="ew" data-edge="{index}">'
                f'{self.svg_edge_inner(edge, self.label_dy.get(index, 0))}</g>')

    def svg_edge_inner(self, edge, dy=0):
        a = self.rect[edge["from"]]
        tone = edge.get("tone") or "k"
        kind = edge.get("kind") or "solid"
        ax, ay, aw, ah = a
        label = edge.get("label")
        parts = []
        if kind == "none":
            sy = ay + ah // 2
            sx = ax + aw
            parts.append(f'<path class="edge off" d="M{sx} {sy} H{sx + 62}"/>')
            parts.append(f'<circle class="nil" cx="{sx + 72}" cy="{sy}" r="6"/>'
                         f'<line class="nil" x1="{sx + 68}" y1="{sy + 4}" x2="{sx + 76}" y2="{sy - 4}"/>')
            return "".join(parts)   # the label is drawn inside the box (see wrap_node)

        b = self.rect[edge["to"]]
        bx, by, bw, bh = b
        cls = f"edge {esc(tone)}" + (" dash" if kind == "dash" else "")
        marker = f"url(#a-{esc(tone)})"
        if ax + aw <= bx:  # left -> right
            sx, sy = ax + aw, ay + ah // 2
            ty = min(max(sy, by + 12), by + bh - 12)
            ty = int(edge.get("to_y", ty))
            ex = bx
            if sy == ty:
                d = f"M{sx} {sy} H{ex}"
            else:
                mx = int(edge.get("elbow_x") or self.default_elbow(edge, sx, ex))
                d = f"M{sx} {sy} H{mx} V{ty} H{ex}"
            lx, ly, anchor = sx + 6, sy - 5 + dy, "start"
            if edge.get("label_at") == "end":
                lx, ly, anchor = ex - 6, ty - 5 + dy, "end"
            elif edge.get("label_at") == "vertical" and sy != ty:
                mx = int(edge.get("elbow_x") or self.default_elbow(edge, sx, ex))
                my = (sy + ty) // 2 + dy
                parts.append(f'<path class="{cls}" d="{d}" marker-end="{marker}"/>')
                lab_cls = f"lab {esc(tone)}" if tone in self.tones else "lab"
                parts.append(f'<text class="{lab_cls}" x="{mx + 6}" y="{my}" '
                             f'transform="rotate(-90 {mx + 6} {my})" text-anchor="middle">{esc(label)}</text>')
                return "".join(parts)
        elif bx + bw <= ax:  # right -> left
            sx, sy = ax, ay + ah // 2
            ty = min(max(sy, by + 12), by + bh - 12)
            ex = bx + bw
            mx = sx - (sx - ex) // 2
            d = f"M{sx} {sy} H{mx} V{ty} H{ex}" if sy != ty else f"M{sx} {sy} H{ex}"
            lx, ly, anchor = sx - 6, sy - 5 + dy, "end"
        else:  # same column: vertical
            sx = ax + aw // 2
            if by >= ay + ah:
                sy, ey = ay + ah, by
            else:
                sy, ey = ay, by + bh
            d = f"M{sx} {sy} V{ey}"
            lx, ly, anchor = sx + 8, (sy + ey) // 2 + 4 + dy, "start"
        parts.append(f'<path class="{cls}" d="{d}" marker-end="{marker}"/>')
        if label:
            parts.append(f'<text class="lab" x="{lx}" y="{ly}" text-anchor="{anchor}">{esc(label)}</text>')
        return "".join(parts)

    def svg(self):
        markers = "".join(
            f'<marker id="a-{esc(t)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
            f'orient="auto-start-reverse"><path class="mk {esc(t)}" d="M0,0 L10,5 L0,10 z"/></marker>'
            for t in list(self.tones) + ["k"])
        heads = "".join(f'<text class="head" x="{self.col_x[c["id"]]}" y="24">{esc(c.get("head"))}</text>'
                        for c in self.columns)
        nodes = "".join(self.svg_node(n) for n in self.nodes)
        edges = "".join(self.svg_edge(e, i) for i, e in enumerate(self.edges))
        legend = self.svg_legend()
        aria = esc(self.d.get("aria") or self.d.get("headline") or "")
        return (f'<svg viewBox="0 0 {self.total_w} {self.total_h}" width="{self.total_w}" height="{self.total_h}" '
                f'role="img" aria-label="{aria}">'
                f'<defs>{markers}</defs>{heads}{nodes}{edges}{legend}</svg>')

    def js_data(self):
        """Geometry the page needs to re-route edges after a node is dragged."""
        edges = [{"from": e.get("from"), "to": e.get("to"),
                  "kind": e.get("kind") or "solid", "tone": e.get("tone") or "k",
                  "label": e.get("label"), "label_at": e.get("label_at"),
                  "to_y": e.get("to_y"), "elbow_x": e.get("elbow_x"),
                  "label_dy": self.label_dy.get(i, 0)} for i, e in enumerate(self.edges)]
        return json.dumps({"rect": {k: list(v) for k, v in self.rect.items()},
                           "col": {n["id"]: n.get("col") for n in self.nodes},
                           "gap": self.gap_after,
                           "tones": list(self.tones),
                           "edges": edges}, ensure_ascii=False)

    def svg_legend(self):
        y = self.legend_y
        items = []
        used = {e.get("tone") for e in self.edges if e.get("kind") != "none"}
        for t in self.tones:
            if t in used and self.tones[t].get("label"):
                items.append((f"edge {t}", self.tones[t]["label"]))
        kinds = {e.get("kind") or "solid" for e in self.edges}
        leg = self.d.get("legend") or {}
        first = next((t for t in self.tones if t in used), "k")
        if "solid" in kinds:
            items.append((f"edge {first}", leg.get("solid", "plein = déclenchement direct")))
        if "dash" in kinds:
            items.append((f"edge {first} dash", leg.get("dash", "tireté = appel explicite")))
        if "none" in kinds:
            items.append(("edge off", leg.get("none", "aucun effet")))
        out, x = [], X0
        for cls, label in items:
            out.append(f'<line class="{cls}" x1="{x}" y1="{y}" x2="{x + 40}" y2="{y}"/>'
                       f'<text class="lab" x="{x + 46}" y="{y + 4}">{esc(label)}</text>')
            x += 46 + int(len(label) * 6.2) + 40
        return "".join(out)

    # ---------------------------------------------------------------- code blocks
    def file_header(self, path, kind, href, note=None):
        link = f'<a href="{esc(href)}" target="_blank" rel="noreferrer">GitHub ↗</a>' if href else ""
        tag = "diff" if kind == "diff" else "hors diff"
        return (f'<div class="fh"><span class="kind {kind}">{tag}</span><span class="path">{esc(path)}</span>'
                f'<span class="note">{esc(note or "")}</span>{link}</div>')

    @staticmethod
    def parse_hunk(hunk):
        rows, o, n = [], 1, 1
        for line in str(hunk).split("\n"):
            m = HUNK_RE.match(line)
            if m:
                o, n = int(m.group(1)), int(m.group(2))
                rows.append(("hunk", "", "", line))
            elif line.startswith("+"):
                rows.append(("add", "", n, line[1:])); n += 1
            elif line.startswith("-"):
                rows.append(("del", o, "", line[1:])); o += 1
            elif line.startswith("\\"):
                rows.append(("meta", "", "", line))
            else:
                rows.append(("ctx", o, n, line[1:] if line[:1] == " " else line)); o += 1; n += 1
        return rows

    def render_diff(self, block):
        path = str(block.get("path", ""))
        rows = []
        for h in block.get("hunks") or []:
            rows.extend(self.parse_hunk(h))
        signs = {"add": "+", "del": "-"}
        tr = "".join(f'<tr class="{k}"><td class="ln">{o}</td><td class="ln">{n}</td>'
                     f'<td class="sg">{signs.get(k, " ")}</td><td>{esc(t)}</td></tr>' for k, o, n, t in rows)
        href = f"{self.pr_url}/files#diff-{hashlib.sha256(path.encode()).hexdigest()}" if self.pr_url else None
        return (f'<div class="code">{self.file_header(path, "diff", href)}'
                f'<div class="scroll"><table class="diff"><tbody>{tr}</tbody></table></div></div>')

    def render_context(self, block):
        path = str(block.get("path", ""))
        segments = block.get("segments") or [block]
        tr, anchor = "", None
        for i, seg in enumerate(segments):
            start = int(seg.get("start_line") or 1)
            hl = {int(n) for n in (seg.get("highlight") or [])}
            if hl and anchor is None:
                anchor = min(hl)
            if i:
                tr += '<tr class="elide"><td class="ln">⋯</td><td></td></tr>'
            for off, text in enumerate(str(seg.get("code", "")).split("\n")):
                num = start + off
                tr += f'<tr{" class=hl" if num in hl else ""}><td class="ln">{num}</td><td>{esc(text)}</td></tr>'
        anchor = anchor or int(segments[0].get("start_line") or 1)
        repo = block.get("repo") or self.repo
        ref = block.get("ref") or self.head
        href = f"https://github.com/{repo}/blob/{ref}/{path}#L{anchor}" if repo and ref else None
        return (f'<div class="code">{self.file_header(path, "ctx", href, block.get("note"))}'
                f'<div class="scroll"><table class="src"><tbody>{tr}</tbody></table></div></div>')

    def render_facts(self, block):
        out = ""
        for item in block.get("items") or []:
            detail = "".join(f"<p>{rich(p)}</p>" for p in as_list(item.get("detail")))
            pre = ""
            if item.get("command"):
                pre = (f'<pre><span class="pr">$ </span>{esc(item["command"])}\n'
                       f'{esc(item.get("output") or "aucun résultat")}</pre>')
            out += f'<div class="fact">— {rich(item.get("text"))}{detail}{pre}</div>'
        return f'<div class="facts">{out}</div>'

    def render_block(self, block):
        kind = block.get("kind")
        if kind == "diff":
            return self.render_diff(block)
        if kind == "facts":
            return self.render_facts(block)
        return self.render_context(block)

    def templates(self):
        out = []
        for node in self.nodes:
            if not (node.get("blocks") or node.get("intro")):
                continue
            title = rich(node.get("modal_title") or node.get("title"))
            intro = "".join(f'<p class="intro">{rich(p)}</p>' for p in as_list(node.get("intro")))
            blocks = "".join(self.render_block(b) for b in node.get("blocks") or [])
            out.append(f'<template data-node="{esc(node["id"])}" data-title="{esc(title)}">{intro}{blocks}</template>')
        return "\n".join(out)

    # ---------------------------------------------------------------- page parts
    def table(self):
        table = self.d.get("table")
        if not table:
            return ""
        head = "".join(f"<th>{rich(h)}</th>" for h in table.get("head") or [])
        rows = ""
        for row in table.get("rows") or []:
            attr = f' data-node="{esc(row["node"])}"' if row.get("node") else ""
            tone = row.get("tone")
            cells = list(row.get("cells") or [])
            first = (f'<span class="dot {esc(tone)}"></span>' if tone else "") + (rich(cells[0]) if cells else "")
            rest = "".join(f"<td>{rich(c)}</td>" for c in cells[1:])
            rows += f"<tr{attr}><td>{first}</td>{rest}</tr>"
        return (f'<section class="table"><h2>{rich(table.get("title"))}</h2><div class="tw"><table>'
                f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div></section>")

    def reading(self):
        items = as_list(self.d.get("reading"))
        if not items:
            return ""
        lis = "".join(f"<li>{rich(i)}</li>" for i in items)
        return (f'<section class="reading"><h2>{rich(self.d.get("reading_title") or "Ce que le schéma montre")}</h2>'
                f"<ul>{lis}</ul></section>")

    def tone_css(self):
        light, dark = [], []
        for key, spec in self.tones.items():
            l_ink, l_soft = spec["light"]
            d_ink, d_soft = spec["dark"]
            light.append(f"--{key}: {l_ink}; --{key}-soft: {l_soft};")
            dark.append(f"--{key}: {d_ink}; --{key}-soft: {d_soft};")
        rules = "".join(
            f".box.{k}{{stroke:var(--{k});fill:var(--{k}-soft)}} text.{k}{{fill:var(--{k})}} "
            f".edge.{k}{{stroke:var(--{k})}} .mk.{k}{{fill:var(--{k})}} .dot.{k}{{background:var(--{k})}}"
            for k in self.tones)
        return " ".join(light), " ".join(dark), rules

    def html(self):
        light, dark, tone_rules = self.tone_css()
        d = self.d
        meta = " · ".join(filter(None, [f'{self.repo}#{d.get("number")}' if self.repo else None,
                                        f"head {self.head[:8]}" if self.head else None]))
        hint = d.get("hint", "Chaque boîte offre deux gestes : la cliquer ouvre le code concerné, hunks du diff "
                              "et extraits hors diff avec les lignes qui comptent surlignées ; cliquer la cible ◎ "
                              "dans son coin isole son sous-flux, tout ce qui mène à elle et tout ce qu'elle atteint, "
                              "le reste passant en gris clair. Une boîte se déplace en la glissant, ce qui dégage les "
                              "étiquettes de flèches qui se recouvrent. Le bouton Réinitialiser rend l'état de départ. "
                              "Échap ferme le code.")
        return f"""<title>{esc(d.get("title") or "PR flow")}</title>
<style>
  :root {{ --bg:#f3f5f8; --surface:#fff; --ink:#1a222d; --muted:#5d6877; --line:#c9d0da; --off:#9aa3ae;
          --note:#fbf5e6; --note-line:#d9c48a; --add-bg:#e4f3e8; --del-bg:#fbe7e4; --hl-bg:#fff1bf; --code-bg:#f7f8fa;
          --focus:#0e6f8e; --danger:#a3271d; {light} }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
    --bg:#10151c; --surface:#181f28; --ink:#e5eaf1; --muted:#9aa6b5; --line:#35404d; --off:#6b7580;
    --note:#2a2517; --note-line:#6b5a2a; --add-bg:#17301f; --del-bg:#3a1d1a; --hl-bg:#3b3312; --code-bg:#121820;
    --focus:#57b8d6; --danger:#e08b82; {dark} }} }}
  :root[data-theme="dark"] {{
    --bg:#10151c; --surface:#181f28; --ink:#e5eaf1; --muted:#9aa6b5; --line:#35404d; --off:#6b7580;
    --note:#2a2517; --note-line:#6b5a2a; --add-bg:#17301f; --del-bg:#3a1d1a; --hl-bg:#3b3312; --code-bg:#121820;
    --focus:#57b8d6; --danger:#e08b82; {dark} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font-family:"IBM Plex Sans","Helvetica Neue",Arial,sans-serif; font-size:15px; line-height:1.5; }}
  code {{ font-family:"IBM Plex Mono","SFMono-Regular",Menlo,monospace; font-size:.92em; }}
  main {{ max-width:1320px; margin:0 auto; padding:32px 24px 56px; }}
  header {{ display:flex; flex-direction:column; gap:6px; margin-bottom:20px; }}
  .eyebrow {{ font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); font-weight:500; }}
  h1 {{ font-size:24px; font-weight:600; margin:0; text-wrap:balance; line-height:1.25; }}
  h1 code {{ font-size:.9em; font-weight:500; }}
  .lede {{ color:var(--muted); max-width:72ch; margin:0; }}
  .hint {{ font-size:13px; color:var(--muted); margin:0; }}
  /* The diagram is a canvas: full viewport width, 1:1 by default so the text keeps its size,
     pan by dragging, zoom with ctrl/⌘ + wheel or the buttons. */
  figure {{ margin:0; width:calc(100vw - 32px); margin-left:calc(50% - 50vw + 16px);
            background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:12px 12px 14px; }}
  .bar {{ display:flex; align-items:center; gap:6px; margin-bottom:8px; font-size:12px; color:var(--muted); }}
  .bar button {{ font:inherit; font-size:12px; color:var(--ink); background:var(--bg); border:1px solid var(--line);
                 border-radius:4px; padding:2px 9px; cursor:pointer; min-width:30px; }}
  .bar button:hover {{ border-color:var(--muted); }}
  .bar .pct {{ min-width:46px; text-align:center; font-family:"IBM Plex Mono",Menlo,monospace; color:var(--ink); }}
  .bar .tip {{ margin-left:auto; }}
  .canvas {{ position:relative; overflow:hidden; height:min(80vh, {self.total_h + 40}px); min-height:320px;
             background:var(--bg); border:1px solid var(--line); border-radius:4px; cursor:grab; touch-action:none; }}
  .canvas.drag {{ cursor:grabbing; }}
  figure:fullscreen {{ width:100vw; margin:0; padding:0; background:var(--bg);
    display:flex; flex-direction:column; border-radius:0; }}
  figure:fullscreen .canvas {{ height:auto; flex:1 1 auto; min-height:0; border-radius:0; border-left:0; border-right:0; }}
  figure:fullscreen figcaption {{ padding:8px 16px; }}
  /* overflow:visible so a box dragged past the diagram's own bounds still paints;
     the frame that clips is .canvas, not the svg viewport. */
  .canvas svg {{ position:absolute; left:0; top:0; transform-origin:0 0; display:block; overflow:visible; }}
  figcaption {{ color:var(--muted); font-size:13px; margin-top:10px; max-width:90ch; }}
  .box {{ fill:var(--surface); stroke:var(--line); stroke-width:1; rx:4; }}
  .box.off {{ stroke:var(--off); stroke-dasharray:3 3; }}
  .box.note {{ fill:var(--note); stroke:var(--note-line); stroke-dasharray:4 3; }}
  .box.band {{ fill:var(--surface); stroke:var(--line); }}
  svg text {{ fill:var(--ink); font-family:"IBM Plex Mono",Menlo,monospace; font-size:11.5px; }}
  svg text.t {{ font-size:13px; font-weight:500; }}
  svg text.sub {{ fill:var(--muted); font-size:10.5px; }}
  svg text.sans {{ font-family:"IBM Plex Sans",Arial,sans-serif; }}
  svg text.head {{ font-family:"IBM Plex Sans",Arial,sans-serif; font-size:11px; letter-spacing:.08em; text-transform:uppercase; fill:var(--muted); font-weight:500; }}
  svg text.lab {{ font-size:10.5px; fill:var(--muted); }}
  svg text.off {{ fill:var(--off); }}
  .edge {{ fill:none; stroke:var(--ink); stroke-width:1.2; }}
  .edge.dash {{ stroke-dasharray:5 4; }}
  .edge.off {{ stroke:var(--off); stroke-dasharray:2 3; }}
  .mk {{ fill:var(--ink); }} .mk.off {{ fill:var(--off); }}
  .nil {{ fill:none; stroke:var(--off); stroke-width:1.4; }}
  {tone_rules}
  g.node {{ cursor:pointer; outline:none; }}
  g.node .box {{ transition:stroke-width .12s, filter .12s; }}
  g.node:hover .box, g.node:focus-visible .box {{ stroke-width:2; filter:drop-shadow(0 1px 3px rgba(0,0,0,.18)); }}
  g.node:focus-visible .box {{ stroke:var(--focus); }}
  g.node .cue {{ fill:var(--muted); font-size:9px; opacity:0; transition:opacity .12s; }}
  g.node:hover .cue, g.node:focus-visible .cue {{ opacity:1; }}
  g.node .iso, g.node .del {{ cursor:pointer; opacity:.4; transition:opacity .12s; }}
  g.node:hover .iso, g.node:focus-visible .iso, g.node.iso-on .iso, .iso:focus-visible,
  g.node:hover .del, g.node:focus-visible .del, .del:focus-visible {{ opacity:1; }}
  .delb {{ fill:var(--surface); stroke:var(--muted); stroke-width:1.2; }}
  .delx {{ fill:none; stroke:var(--muted); stroke-width:1.4; stroke-linecap:round; }}
  .del:hover .delb {{ stroke:var(--danger); }} .del:hover .delx {{ stroke:var(--danger); }}
  g.node.gone, g.ew.gone {{ display:none; }}
  .isob {{ fill:var(--surface); stroke:var(--muted); stroke-width:1.2; }}
  .isod {{ fill:var(--muted); }}
  .iso:hover .isob, g.node.iso-on .isob {{ stroke:var(--focus); }}
  .iso:hover .isod, g.node.iso-on .isod {{ fill:var(--focus); }}
  g.node.iso-on .box {{ stroke-width:2.4; }}
  g.node.dim, g.ew.dim {{ opacity:.12; filter:grayscale(1); }}
  g.node.dim {{ pointer-events:none; }}
  g.node.moved .box {{ stroke-dasharray:none; }}
  .canvas.nodedrag, .canvas.nodedrag * {{ cursor:grabbing; }}
  @media (prefers-reduced-motion: reduce) {{ g.node .iso {{ transition:none; }} }}
  @media (prefers-reduced-motion: reduce) {{ g.node .box, g.node .cue {{ transition:none; }} }}
  section.table {{ margin-top:28px; }}
  h2 {{ font-size:16px; font-weight:600; margin:0 0 10px; }}
  .tw {{ overflow-x:auto; background:var(--surface); border:1px solid var(--line); border-radius:6px; }}
  .tw table {{ border-collapse:collapse; width:100%; font-size:13.5px; min-width:900px; }}
  .tw th, .tw td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
  .tw th {{ font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); font-weight:500; }}
  .tw tr:last-child td {{ border-bottom:0; }}
  .tw tr[data-node] {{ cursor:pointer; }} .tw tr[data-node]:hover td {{ background:var(--code-bg); }}
  .dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }} .dot.off {{ background:var(--off); }}
  .reading {{ margin-top:24px; max-width:78ch; }} .reading ul {{ padding-left:20px; margin:6px 0 0; }} .reading li {{ margin:4px 0; }}
  dialog {{ border:1px solid var(--line); border-radius:8px; padding:0; background:var(--surface); color:var(--ink); width:min(1120px,94vw); max-height:88vh; }}
  dialog::backdrop {{ background:rgba(10,14,20,.55); }}
  .mh {{ display:flex; align-items:baseline; gap:12px; padding:14px 18px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--surface); z-index:1; }}
  .mh h3 {{ margin:0; font-size:16px; font-weight:600; flex:1; }} .mh h3 code {{ font-weight:500; }}
  .mh button {{ font:inherit; font-size:13px; color:var(--muted); background:none; border:1px solid var(--line); border-radius:4px; padding:3px 10px; cursor:pointer; }}
  .mh button:hover, .mh button:focus-visible {{ color:var(--ink); border-color:var(--focus); outline:none; }}
  .mb {{ padding:14px 18px 22px; overflow:auto; max-height:calc(88vh - 56px); display:flex; flex-direction:column; gap:14px; }}
  .mb .intro {{ margin:0; max-width:90ch; color:var(--muted); font-size:14px; }} .mb .intro code {{ color:var(--ink); }}
  .code {{ border:1px solid var(--line); border-radius:6px; overflow:hidden; background:var(--code-bg); }}
  .fh {{ display:flex; gap:12px; align-items:baseline; padding:6px 10px; border-bottom:1px solid var(--line); font-size:12px; background:var(--surface); }}
  .fh .path {{ font-family:"IBM Plex Mono",Menlo,monospace; font-weight:500; }} .fh .note {{ color:var(--muted); flex:1; }}
  .fh .kind {{ font-size:10px; letter-spacing:.06em; text-transform:uppercase; border:1px solid currentColor; border-radius:3px; padding:0 5px; color:var(--muted); }}
  .fh .kind.diff {{ color:var(--focus); }}
  .fh a {{ color:var(--muted); font-size:12px; text-decoration:none; }} .fh a:hover {{ color:var(--ink); text-decoration:underline; }}
  table.src, table.diff {{ width:100%; font-family:"IBM Plex Mono",Menlo,monospace; font-size:12px; line-height:1.45; border-collapse:collapse; }}
  table.src td, table.diff td {{ padding:0 8px; border:0; white-space:pre; vertical-align:top; }}
  td.ln {{ color:var(--muted); text-align:right; user-select:none; width:1%; font-variant-numeric:tabular-nums; }}
  td.sg {{ width:1%; user-select:none; color:var(--muted); padding:0 4px; }}
  tr.add td {{ background:var(--add-bg); }} tr.del td {{ background:var(--del-bg); }}
  tr.hunk td, tr.meta td {{ color:var(--muted); font-style:italic; background:var(--surface); }}
  tr.hl td {{ background:var(--hl-bg); }} tr.elide td {{ color:var(--muted); text-align:center; }}
  .facts {{ display:flex; flex-direction:column; gap:6px; }} .fact {{ font-size:14px; }} .fact p {{ margin:4px 0 0; color:var(--muted); }}
  .fact pre {{ margin:6px 0 0; padding:8px 10px; background:var(--code-bg); border:1px solid var(--line); border-radius:4px; font-size:12px; line-height:1.45; overflow-x:auto; white-space:pre; }}
  .fact .pr {{ color:var(--muted); }}
</style>
<main>
  <header>
    <div class="eyebrow">{esc(meta)}</div>
    <h1>{rich(d.get("headline"))}</h1>
    {"".join(f'<p class="lede">{rich(p)}</p>' for p in as_list(d.get("lede")))}
    {f'<p class="hint">{rich(hint)}</p>' if hint else ""}
  </header>
  <figure>
    <div class="bar">
      <button type="button" data-zoom="out" aria-label="Zoom arrière">−</button>
      <span class="pct" id="pct">100 %</span>
      <button type="button" data-zoom="in" aria-label="Zoom avant">+</button>
      <button type="button" data-zoom="one">100 %</button>
      <button type="button" data-zoom="fit">Ajuster</button>
      <button type="button" data-act="reset" id="reset">Réinitialiser</button>
      <button type="button" data-act="full" id="full">Plein écran</button>
      <span class="tip">cliquer une boîte : son code · ◎ : isoler son sous-flux · × : la retirer avec ce qui en dépend · glisser pour déplacer · Ctrl + molette : zoom · plein écran</span>
    </div>
    <div class="canvas" id="canvas">{self.svg()}</div>
    {f'<figcaption>{rich(d.get("caption"))}</figcaption>' if d.get("caption") else ""}</figure>
  {self.table()}
  {self.reading()}
</main>
<dialog id="dlg" aria-labelledby="dlg-title">
  <div class="mh"><h3 id="dlg-title"></h3><button type="button" id="dlg-close">Fermer</button></div>
  <div class="mb" id="dlg-body"></div>
</dialog>
{self.templates()}
<script>
(function () {{
  var DATA = {self.js_data()};
  var RECT = DATA.rect, NCOL = DATA.col, GAP = DATA.gap, TONES = DATA.tones, EDGES = DATA.edges;
  var OFF = {{}};
  var dlg = document.getElementById('dlg'), body = document.getElementById('dlg-body'), title = document.getElementById('dlg-title');
  var canvas = document.getElementById('canvas'), svg = canvas.querySelector('svg'), pct = document.getElementById('pct');
  var nodeEls = {{}}, edgeEls = {{}};
  document.querySelectorAll('g.node').forEach(function (g) {{ nodeEls[g.getAttribute('data-node')] = g; }});
  document.querySelectorAll('g.ew').forEach(function (g) {{ edgeEls[g.getAttribute('data-edge')] = g; }});

  function open(id) {{
    var tpl = document.querySelector('template[data-node="' + id + '"]');
    if (!tpl) return;
    title.innerHTML = tpl.getAttribute('data-title') || id;
    body.innerHTML = ''; body.appendChild(tpl.content.cloneNode(true)); body.scrollTop = 0;
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
  }}

  /* ---- edge routing, ported from the Python layout so a dragged box keeps its wires ---- */
  function xesc(s) {{ return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }}
  function offOf(id) {{ return OFF[id] || [0, 0]; }}
  function rectOf(id) {{ var r = RECT[id], o = offOf(id); return [r[0] + o[0], r[1] + o[1], r[2], r[3]]; }}
  function elbowOf(e, sx, ex) {{
    var g = GAP[NCOL[e.from]];
    if (g !== undefined && sx + g < ex) return sx + Math.floor(g / 2);
    return sx + Math.floor((ex - sx) / 2);
  }}
  function given(v) {{ return v !== null && v !== undefined; }}
  function labCls(tone) {{ return 'lab' + (TONES.indexOf(tone) >= 0 ? ' ' + tone : ''); }}

  function edgeHTML(e) {{
    var a = rectOf(e.from), ax = a[0], ay = a[1], aw = a[2], ah = a[3];
    var tone = e.tone || 'k', kind = e.kind || 'solid', label = e.label, dy = e.label_dy || 0;
    if (kind === 'none') {{
      var ny = ay + Math.floor(ah / 2), nx = ax + aw;
      return '<path class="edge off" d="M' + nx + ' ' + ny + ' H' + (nx + 62) + '"/>'
           + '<circle class="nil" cx="' + (nx + 72) + '" cy="' + ny + '" r="6"/>'
           + '<line class="nil" x1="' + (nx + 68) + '" y1="' + (ny + 4) + '" x2="' + (nx + 76) + '" y2="' + (ny - 4) + '"/>';
    }}
    var b = rectOf(e.to), bx = b[0], by = b[1], bw = b[2], bh = b[3];
    var cls = 'edge ' + tone + (kind === 'dash' ? ' dash' : '');
    var marker = 'url(#a-' + tone + ')';
    var d, lx, ly, anchor;
    if (ax + aw <= bx) {{
      var sx = ax + aw, sy = ay + Math.floor(ah / 2), ex = bx;
      var ty = Math.min(Math.max(sy, by + 12), by + bh - 12);
      if (given(e.to_y)) ty = Math.trunc(e.to_y) + offOf(e.to)[1];
      var mx = given(e.elbow_x) ? Math.trunc(e.elbow_x) : elbowOf(e, sx, ex);
      d = (sy === ty) ? ('M' + sx + ' ' + sy + ' H' + ex)
                      : ('M' + sx + ' ' + sy + ' H' + mx + ' V' + ty + ' H' + ex);
      lx = sx + 6; ly = sy - 5 + dy; anchor = 'start';
      if (e.label_at === 'end') {{ lx = ex - 6; ly = ty - 5 + dy; anchor = 'end'; }}
      else if (e.label_at === 'vertical' && sy !== ty) {{
        var my = Math.floor((sy + ty) / 2) + dy;
        return '<path class="' + cls + '" d="' + d + '" marker-end="' + marker + '"/>'
             + '<text class="' + labCls(tone) + '" x="' + (mx + 6) + '" y="' + my
             + '" transform="rotate(-90 ' + (mx + 6) + ' ' + my + ')" text-anchor="middle">' + xesc(label) + '</text>';
      }}
    }} else if (bx + bw <= ax) {{
      var sx2 = ax, sy2 = ay + Math.floor(ah / 2), ex2 = bx + bw;
      var ty2 = Math.min(Math.max(sy2, by + 12), by + bh - 12);
      var mx2 = sx2 - Math.floor((sx2 - ex2) / 2);
      d = (sy2 !== ty2) ? ('M' + sx2 + ' ' + sy2 + ' H' + mx2 + ' V' + ty2 + ' H' + ex2)
                        : ('M' + sx2 + ' ' + sy2 + ' H' + ex2);
      lx = sx2 - 6; ly = sy2 - 5 + dy; anchor = 'end';
    }} else {{
      var sx3 = ax + Math.floor(aw / 2), sy3, ey3;
      if (by >= ay + ah) {{ sy3 = ay + ah; ey3 = by; }} else {{ sy3 = ay; ey3 = by + bh; }}
      d = 'M' + sx3 + ' ' + sy3 + ' V' + ey3;
      lx = sx3 + 8; ly = Math.floor((sy3 + ey3) / 2) + 4 + dy; anchor = 'start';
    }}
    var out = '<path class="' + cls + '" d="' + d + '" marker-end="' + marker + '"/>';
    if (label) out += '<text class="lab" x="' + lx + '" y="' + ly + '" text-anchor="' + anchor + '">' + xesc(label) + '</text>';
    return out;
  }}
  function reroute(id) {{
    EDGES.forEach(function (e, i) {{
      if (e.from !== id && e.to !== id) return;
      if (edgeEls[i]) edgeEls[i].innerHTML = edgeHTML(e);
    }});
  }}
  function rerouteAll() {{ EDGES.forEach(function (e, i) {{ if (edgeEls[i]) edgeEls[i].innerHTML = edgeHTML(e); }}); }}

  /* ---- removal: a box goes, and with it whatever no longer has a reason to be drawn.
     A box is dropped when it used to have incoming edges and has none left (nothing reaches
     it any more), or used to have outgoing edges and has none left (it leads nowhere). Boxes
     that never had one side keep their place: origins have no input, effects no output. ---- */
  var GONE = {{}}, ORIG_IN = {{}}, ORIG_OUT = {{}};
  EDGES.forEach(function (e) {{
    ORIG_OUT[e.from] = (ORIG_OUT[e.from] || 0) + 1;
    if (e.to) ORIG_IN[e.to] = (ORIG_IN[e.to] || 0) + 1;
  }});
  function edgeAlive(e) {{ return !GONE[e.from] && !(e.to && GONE[e.to]); }}
  function cascade() {{
    var moved = true;
    while (moved) {{
      moved = false;
      Object.keys(nodeEls).forEach(function (n) {{
        if (GONE[n]) return;
        var inn = 0, out = 0;
        EDGES.forEach(function (e) {{
          if (!edgeAlive(e)) return;
          if (e.to === n) inn++;
          if (e.from === n) out++;
        }});
        if ((ORIG_IN[n] > 0 && inn === 0) || (ORIG_OUT[n] > 0 && out === 0)) {{ GONE[n] = 1; moved = true; }}
      }});
    }}
  }}
  function paintGone() {{
    Object.keys(nodeEls).forEach(function (n) {{ nodeEls[n].classList.toggle('gone', !!GONE[n]); }});
    EDGES.forEach(function (e, i) {{ if (edgeEls[i]) edgeEls[i].classList.toggle('gone', !edgeAlive(e)); }});
  }}
  function drop(id) {{
    if (GONE[id]) return;
    GONE[id] = 1; cascade(); paintGone();
    if (isoId) {{
      if (GONE[isoId]) {{ clearIso(); }}
      else {{ var keep = isoId; clearIso(); isolate(keep); }}
    }}
  }}

  /* ---- subflow: everything that leads to a box and everything it reaches ---- */
  function adjacency() {{
    var f = {{}}, b = {{}};
    EDGES.forEach(function (e) {{
      if (!e.to || !edgeAlive(e)) return;
      (f[e.from] = f[e.from] || []).push(e.to);
      (b[e.to] = b[e.to] || []).push(e.from);
    }});
    return [f, b];
  }}
  function reach(id, map) {{
    var seen = {{}}, stack = [id];
    while (stack.length) {{
      var cur = stack.pop();
      (map[cur] || []).forEach(function (n) {{ if (!seen[n]) {{ seen[n] = 1; stack.push(n); }} }});
    }}
    return seen;
  }}
  var isoId = null;
  function clearIso() {{
    isoId = null;
    Object.keys(nodeEls).forEach(function (k2) {{ nodeEls[k2].classList.remove('dim', 'iso-on'); }});
    Object.keys(edgeEls).forEach(function (k2) {{ edgeEls[k2].classList.remove('dim'); }});
  }}
  function isolate(id) {{
    if (isoId === id) {{ clearIso(); return; }}
    clearIso(); isoId = id;
    var ab = adjacency(), set = {{}}; set[id] = 1;
    Object.keys(reach(id, ab[0])).forEach(function (n) {{ set[n] = 1; }});
    Object.keys(reach(id, ab[1])).forEach(function (n) {{ set[n] = 1; }});
    Object.keys(nodeEls).forEach(function (n) {{ if (!set[n] && !GONE[n]) nodeEls[n].classList.add('dim'); }});
    if (nodeEls[id]) nodeEls[id].classList.add('iso-on');
    EDGES.forEach(function (e, i) {{
      var keep = e.to ? (set[e.from] && set[e.to]) : !!set[e.from];
      if (!keep && edgeEls[i]) edgeEls[i].classList.add('dim');
    }});
  }}

  /* ---- canvas: pan and zoom, 1:1 by default ---- */
  var W = {self.total_w}, H = {self.total_h}, k = 1, tx = 0, ty = 0, dragged = false;
  function apply() {{
    svg.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + k + ')';
    pct.textContent = Math.round(k * 100) + ' %';
  }}
  /* Panning is deliberately unbounded: the diagram may be dragged clean off the frame.
     `home` is the starting placement only, used by the 100 %, Ajuster and Réinitialiser
     buttons — centred when the diagram is smaller than the canvas, top-left otherwise. */
  function home() {{
    var cw = canvas.clientWidth, ch = canvas.clientHeight;
    tx = W * k < cw ? (cw - W * k) / 2 : 0;
    ty = H * k < ch ? (ch - H * k) / 2 : 0;
  }}
  function setZoom(nk, px, py) {{
    nk = Math.min(3, Math.max(0.25, nk));
    if (px === undefined) {{ px = canvas.clientWidth / 2; py = canvas.clientHeight / 2; }}
    tx = px - (px - tx) * (nk / k); ty = py - (py - ty) * (nk / k); k = nk;
    apply();
  }}
  function fit() {{ k = Math.min(canvas.clientWidth / W, canvas.clientHeight / H); home(); apply(); }}
  function one() {{ k = 1; home(); apply(); }}
  function resetAll() {{
    clearIso();
    GONE = {{}}; paintGone();
    Object.keys(OFF).forEach(function (id) {{
      var g = nodeEls[id];
      if (g) {{ g.setAttribute('transform', 'translate(0,0)'); g.classList.remove('moved'); }}
    }});
    OFF = {{}}; rerouteAll(); one();
  }}
  var fig = canvas.closest('figure'), fullBtn = document.getElementById('full');
  function toggleFull() {{
    if (document.fullscreenElement) {{ if (document.exitFullscreen) document.exitFullscreen(); }}
    else if (fig.requestFullscreen) {{ var r = fig.requestFullscreen(); if (r && r.catch) r.catch(function () {{}}); }}
  }}
  document.addEventListener('fullscreenchange', function () {{
    var on = document.fullscreenElement === fig;
    fullBtn.textContent = on ? 'Quitter le plein écran' : 'Plein écran';
    apply();
  }});
  document.querySelectorAll('.bar button').forEach(function (b) {{
    b.addEventListener('click', function () {{
      if (b.getAttribute('data-act') === 'full') {{ toggleFull(); return; }}
      if (b.getAttribute('data-act') === 'reset') {{ resetAll(); return; }}
      var z = b.getAttribute('data-zoom');
      if (z === 'in') setZoom(k * 1.25); else if (z === 'out') setZoom(k / 1.25); else if (z === 'fit') fit(); else one();
    }});
  }});
  canvas.addEventListener('wheel', function (e) {{
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    var r = canvas.getBoundingClientRect();
    setZoom(k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), e.clientX - r.left, e.clientY - r.top);
  }}, {{ passive: false }});
  canvas.addEventListener('dblclick', function (e) {{ if (!e.target.closest('[data-node]')) one(); }});

  /* No pointer capture: a captured pointer sends the click to the canvas, and the boxes
     would no longer open. The drag is followed on window instead. */
  var start = null, nodeDrag = null;
  document.addEventListener('pointerdown', function () {{ dragged = false; }}, true);
  canvas.addEventListener('pointerdown', function (e) {{
    if (e.button !== 0) return;
    if (e.target.closest('.iso') || e.target.closest('.del')) return;
    var g = e.target.closest('g.node');
    if (g) {{
      var id = g.getAttribute('data-node'), o = offOf(id);
      nodeDrag = {{ id: id, g: g, x: e.clientX, y: e.clientY, ox: o[0], oy: o[1] }};
    }} else {{
      start = {{ x: e.clientX, y: e.clientY, tx: tx, ty: ty }};
    }}
    e.preventDefault();
  }});
  window.addEventListener('pointermove', function (e) {{
    if (nodeDrag) {{
      var ndx = e.clientX - nodeDrag.x, ndy = e.clientY - nodeDrag.y;
      if (!dragged && Math.abs(ndx) + Math.abs(ndy) < 4) return;
      dragged = true; canvas.classList.add('nodedrag');
      var nx = nodeDrag.ox + ndx / k, ny = nodeDrag.oy + ndy / k;
      OFF[nodeDrag.id] = [nx, ny];
      nodeDrag.g.setAttribute('transform', 'translate(' + nx + ',' + ny + ')');
      nodeDrag.g.classList.add('moved');
      reroute(nodeDrag.id);
      return;
    }}
    if (!start) return;
    var dx = e.clientX - start.x, dy = e.clientY - start.y;
    if (!dragged && Math.abs(dx) + Math.abs(dy) < 4) return;
    dragged = true; canvas.classList.add('drag');
    tx = start.tx + dx; ty = start.ty + dy; apply();
  }});
  function endDrag() {{
    if (nodeDrag) {{ nodeDrag = null; canvas.classList.remove('nodedrag'); return; }}
    if (!start) return;
    start = null; canvas.classList.remove('drag'); apply();
  }}
  window.addEventListener('pointerup', endDrag);
  window.addEventListener('pointercancel', endDrag);
  window.addEventListener('resize', apply);
  one();

  document.querySelectorAll('[data-node]').forEach(function (el) {{
    if (el.tagName.toLowerCase() === 'template') return;
    el.addEventListener('click', function () {{ if (dragged) return; open(el.getAttribute('data-node')); }});
    el.addEventListener('keydown', function (e) {{ if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); open(el.getAttribute('data-node')); }} }});
  }});
  document.querySelectorAll('.iso').forEach(function (el) {{
    el.addEventListener('click', function (e) {{ e.stopPropagation(); if (dragged) return; isolate(el.getAttribute('data-iso')); }});
    el.addEventListener('keydown', function (e) {{
      if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); e.stopPropagation(); isolate(el.getAttribute('data-iso')); }}
    }});
  }});
  document.querySelectorAll('.del').forEach(function (el) {{
    el.addEventListener('click', function (e) {{ e.stopPropagation(); if (dragged) return; drop(el.getAttribute('data-del')); }});
    el.addEventListener('keydown', function (e) {{
      if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); e.stopPropagation(); drop(el.getAttribute('data-del')); }}
    }});
  }});
  window.addEventListener('keydown', function (e) {{
    if (e.key === 'Escape' && !dlg.open && !document.fullscreenElement && isoId) clearIso();
  }});
  document.getElementById('dlg-close').addEventListener('click', function () {{ dlg.close(); }});
  dlg.addEventListener('click', function (e) {{ if (e.target === dlg) dlg.close(); }});
}})();
</script>
"""


def open_in_browser(path):
    """Open the rendered page with the platform's default browser (Linux, macOS, Windows)."""
    import os
    import webbrowser
    webbrowser.open("file://" + os.path.abspath(path))


def main():
    args = [a for a in sys.argv[1:] if a != "--open"]
    if len(args) != 2:
        sys.exit("usage: render.py flow.json out.html [--open]")
    src, dst = args
    with open(src, encoding="utf-8") as handle:
        data = json.load(handle)
    flow = Flow(data)
    # every edge endpoint must be a node
    ids = {n["id"] for n in flow.nodes}
    for e in flow.edges:
        for end in ("from", "to"):
            if e.get(end) and e[end] not in ids:
                sys.exit(f"edge references unknown node: {e[end]!r}")
    for row in (data.get("table") or {}).get("rows") or []:
        if row.get("node") and row["node"] not in ids:
            sys.exit(f"table row references unknown node: {row['node']!r}")
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as out:
        out.write(flow.html())
    print(dst)
    if "--open" in sys.argv:
        open_in_browser(dst)


if __name__ == "__main__":
    main()
