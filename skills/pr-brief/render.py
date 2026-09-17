#!/usr/bin/env python3
"""Render a PR comprehension brief (JSON) into a self-contained HTML file.

    python3 render.py brief.json out.html

See SKILL.md for the JSON shape.
"""

import hashlib
import html
import json
import re
import sys

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def rich(value):
    """Escape, then turn `backticked` spans into inline code."""
    return INLINE_CODE_RE.sub(r"<code>\1</code>", esc(value))


def anchor_for(kind, text):
    """Content-derived anchor: stable across re-renders while the text is unchanged.

    When the text does change the comment detaches on purpose — it was written about
    something that no longer says the same thing.
    """
    digest = hashlib.sha1(f"{kind}|{str(text)[:160]}".encode()).hexdigest()[:12]
    return f"{kind}:{digest}"


def as_paragraphs(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


class Brief:
    def __init__(self, data):
        self.data = data
        self.repo = str(data.get("repo", ""))
        self.number = data.get("number", "")
        self.head_sha = str(data.get("head_sha", ""))
        self.pr_url = str(data.get("url", ""))

    # -- links ------------------------------------------------------------

    def diff_link(self, path):
        if not self.pr_url:
            return None
        anchor = "diff-" + hashlib.sha256(path.encode()).hexdigest()
        return f"{self.pr_url}/files#{anchor}"

    def blob_link(self, path, line, repo=None, ref=None):
        """A block may point at another repo or another ref (the *before* state, master…)."""
        repo = repo or self.repo
        ref = ref or self.head_sha
        if not repo or not ref:
            return None
        return f"https://github.com/{repo}/blob/{ref}/{path}#L{line}"

    def file_header(self, path, href, note=None):
        link = (
            f'<a class="gh" href="{esc(href)}" target="_blank" rel="noreferrer">GitHub</a>'
            if href
            else ""
        )
        extra = f'<span class="note">{esc(note)}</span>' if note else ""
        return f'<div class="fh"><span class="path">{esc(path)}</span>{extra}{link}</div>'

    # -- code blocks ------------------------------------------------------

    @staticmethod
    def parse_hunk(hunk):
        rows, old_n, new_n = [], 1, 1
        for line in str(hunk).split("\n"):
            match = HUNK_RE.match(line)
            if match:
                old_n, new_n = int(match.group(1)), int(match.group(2))
                rows.append(("hunk", "", "", line))
            elif line.startswith("+"):
                rows.append(("add", "", new_n, line[1:]))
                new_n += 1
            elif line.startswith("-"):
                rows.append(("del", old_n, "", line[1:]))
                old_n += 1
            elif line.startswith("\\"):
                rows.append(("meta", "", "", line))
            else:
                rows.append(("ctx", old_n, new_n, line[1:] if line[:1] == " " else line))
                old_n += 1
                new_n += 1
        return rows

    def render_diff(self, block):
        path = str(block.get("path", ""))
        rows = []
        for hunk in block.get("hunks") or []:
            rows.extend(self.parse_hunk(hunk))
        signs = {"add": "+", "del": "-"}
        cells = []
        for kind, old_n, new_n, text in rows:
            attrs = f' class="{kind}"'
            if kind in ("add", "ctx") and new_n:
                attrs += (f' data-anchor="c:{esc(path)}:{new_n}" data-path="{esc(path)}"'
                          f' data-line="{new_n}" data-side="RIGHT"')
            cells.append(
                f'<tr{attrs}><td class="ln">{old_n}</td><td class="ln">{new_n}</td>'
                f'<td class="sg">{signs.get(kind, " ")}</td><td class="cd">{esc(text)}</td></tr>'
            )
        lines = "".join(cells)
        return (
            '<div class="code">'
            + self.file_header(path, self.diff_link(path))
            + f'<div class="scroll"><table class="diff"><tbody>{lines}</tbody></table></div></div>'
        )

    def render_context(self, block):
        path = str(block.get("path", ""))
        # `segments` lets an excerpt skip the middle of a file without lying about line numbers
        segments = block.get("segments") or [block]
        lines, anchor = "", None
        for index, segment in enumerate(segments):
            if index:
                lines += '<tr class="elide"><td class="ln">\u22ef</td><td class="cd"></td></tr>'
            seg_start = int(segment.get("start_line") or 1)
            highlight = {int(n) for n in (segment.get("highlight") or [])}
            if highlight and anchor is None:
                anchor = min(highlight)
            for offset, text in enumerate(str(segment.get("code", "")).split("\n")):
                number = seg_start + offset
                cls = ' class="hl"' if number in highlight else ""
                lines += (f'<tr{cls} data-anchor="c:{esc(path)}:{number}" data-path="{esc(path)}"'
                          f' data-line="{number}" data-side="RIGHT">'
                          f'<td class="ln">{number}</td><td class="cd">{esc(text)}</td></tr>')
        if anchor is None:
            anchor = int(segments[0].get("start_line") or 1)
        note = block.get("note") or "hors diff"
        href = self.blob_link(path, anchor, block.get("repo"), block.get("ref"))
        label = f"{block['repo']} — {path}" if block.get("repo") else path
        return (
            '<div class="code ctxblock">'
            + self.file_header(label, href, note)
            + f'<div class="scroll"><table class="src"><tbody>{lines}</tbody></table></div></div>'
        )

    def render_facts(self, block):
        """A list of one-line facts; each expands only if it carries a detail or a command."""
        rows = []
        for item in block.get("items") or []:
            text = rich(item.get("text"))
            detail = "".join(f"<p>{rich(p)}</p>" for p in as_paragraphs(item.get("detail")))
            command = item.get("command")
            shell = ""
            if command:
                output = item.get("output") or "aucun résultat"
                shell = (f'<pre class="cmd"><span class="pr">$</span> {esc(command)}\n'
                         f'<span class="out">{esc(output)}</span></pre>')
            anchor = anchor_for("f", item.get("text"))
            if detail or shell:
                rows.append(f'<details class="fact" data-anchor="{anchor}">'
                            f'<summary>{text}</summary>'
                            f'<div class="factbody">{detail}{shell}</div></details>')
            else:
                rows.append(f'<div class="fact flat" data-anchor="{anchor}">'
                            f'<span class="bullet">—</span>{text}</div>')
        return f'<div class="facts">{"".join(rows)}</div>'

    def render_block(self, block):
        kind = block.get("kind")
        if kind == "diff":
            return self.render_diff(block)
        if kind == "facts":
            return self.render_facts(block)
        return self.render_context(block)

    # -- steps ------------------------------------------------------------

    def render_step(self, step, index, prefix):
        prose = "".join(
            f'<p data-anchor="{anchor_for("p", para)}">{rich(para)}</p>'
            for para in as_paragraphs(step.get("prose"))
        )
        blocks = "".join(self.render_block(b) for b in step.get("blocks") or [])
        return (
            f'<section class="step" id="{prefix}-{index}">'
            f'<h3><span class="num">{index}</span>{rich(step.get("title"))}</h3>'
            f'<div class="prose">{prose}</div>{blocks}</section>'
        )

    def render_steps(self, key, prefix):
        steps = self.data.get(key) or []
        return "".join(
            self.render_step(step, i + 1, prefix) for i, step in enumerate(steps)
        )

    # -- side sections ----------------------------------------------------

    def render_pending(self):
        point = self.data.get("pending_point")
        if not point:
            return ""
        link = (
            f' <a class="gh" href="{esc(point.get("url"))}" target="_blank" rel="noreferrer">le fil</a>'
            if point.get("url")
            else ""
        )
        return (
            '<section class="step pending" id="suspens">'
            "<h3>Point structurant en suspens</h3>"
            f'<div class="prose"><p>{rich(point.get("title"))}</p></div>'
            f'<blockquote>{rich(point.get("quote"))}'
            f'<footer>— {esc(point.get("author"))}{link}</footer></blockquote></section>'
        )

    def render_noise(self):
        files = self.data.get("noise") or []
        if not files:
            return ""
        rows = ""
        total = 0
        for entry in files:
            count = int(entry.get("count") or 1)
            total += count
            tally = f'<span class="n">{count} fichiers</span>' if count > 1 else ""
            rows += (f'<li><span class="path">{esc(entry.get("label") or entry.get("path"))}</span>'
                     f'{tally}<span class="dl">+{int(entry.get("additions") or 0)} '
                     f'−{int(entry.get("deletions") or 0)}</span></li>')
        plural = "s" if total > 1 else ""
        return (
            '<section class="step" id="bruit"><details>'
            f"<summary>Bruit — {total} fichier{plural} sans enjeu de lecture</summary>"
            f'<ul class="noise">{rows}</ul></details></section>'
        )

    def render_my_feedback(self):
        """Only MY own threads: what I said, the replies, and the code as it stands now.

        Other reviewers' change requests are deliberately absent — I read those on GitHub.
        Nothing here says whether a point was addressed: it shows the current code at the
        anchor and lets me decide.
        """
        feedback = self.data.get("my_feedback")
        threads = (feedback or {}).get("threads") or []
        if not threads:
            return ""

        rows = []
        for thread in threads:
            tags = "".join(f'<span class="tag {esc(t)}">{esc(t)}</span>'
                           for t in (thread.get("tags") or []))
            where = thread.get("path") or ""
            if thread.get("line"):
                where += f":{thread['line']}"
            body = str(thread.get("body") or "").strip().replace("\n", " ")
            lead = body if len(body) <= 150 else body[:149].rsplit(" ", 1)[0] + "…"
            head = (f'<span class="where">{esc(where)}</span>{tags}'
                    f'<span class="lead">{rich(lead)}</span>')
            inner = f'<div class="full">{rich(body)}</div>' if len(body) > 150 else ""
            for reply in thread.get("replies") or []:
                inner += (f'<div class="reply"><span class="who">{esc(reply.get("author"))}</span>'
                          f'{rich(reply.get("body"))}</div>')
            if not thread.get("replies"):
                inner += '<p class="noreply">Aucune réponse.</p>'
            inner += "".join(self.render_block(b) for b in thread.get("blocks") or [])
            rows.append(f'<details class="thread"><summary>{head}</summary>'
                        f'<div class="threadbody">{inner}</div></details>')

        count = len(threads)
        plural = "s" if count > 1 else ""
        note = f'<p class="revnote">{rich(feedback.get("note"))}</p>' if feedback.get("note") else ""
        return ('<h2 id="retours">Mes retours précédents'
                f'<span class="sub">{count} fil{plural}, et le code tel qu\'il est aujourd\'hui</span></h2>'
                f'<section class="step review" id="retours-section">{note}'
                f'<div class="threads">{"".join(rows)}</div></section>')

    def render_toc(self):
        items = ['<li class="sec">Parcours</li>']
        for i, step in enumerate(self.data.get("steps") or []):
            items.append(
                f'<li><a href="#etape-{i + 1}"><span class="num">{i + 1}</span>'
                f'<span class="lbl">{rich(step.get("title"))}</span></a></li>'
            )
        impact = self.data.get("impact") or []
        if impact:
            items.append('<li class="sec">Rayon d\'impact</li>')
            for i, step in enumerate(impact):
                items.append(
                    f'<li><a href="#impact-{i + 1}"><span class="num">·</span>'
                    f'<span class="lbl">{rich(step.get("title"))}</span></a></li>'
                )
        if (self.data.get("my_feedback") or {}).get("threads"):
            items.append('<li class="sec"><a href="#retours">Mes retours</a></li>')
        if self.data.get("noise"):
            items.append('<li class="sec"><a href="#bruit">Bruit</a></li>')
        return "".join(items)


CSS = """
:root {
  --bg:#fff; --fg:#1f2328; --muted:#636c76; --line:#d1d9e0; --soft:#f6f8fa;
  --accent:#0969da; --add-bg:#e6ffec; --add-ln:#ccffd8; --del-bg:#ffebe9; --del-ln:#ffd7d5;
  --hunk:#57606a; --hunk-bg:#f6f8fa; --hl:#fff8c5; --plus:#1a7f37; --minus:#cf222e;
  --inline:rgba(129,139,152,.16); --cmt-bg:rgba(255,212,0,.28); --cmt-on:rgba(255,212,0,.55); --cmt-line:#d4a72c;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
}
@media (prefers-color-scheme:dark) {
  :root {
    --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#3d444d; --soft:#151b23;
    --accent:#4493f8; --add-bg:rgba(46,160,67,.15); --add-ln:rgba(46,160,67,.3);
    --del-bg:rgba(248,81,73,.13); --del-ln:rgba(248,81,73,.3);
    --hunk:#9198a1; --hunk-bg:#151b23; --hl:rgba(187,128,9,.2);
    --plus:#3fb950; --minus:#f85149; --inline:rgba(110,118,129,.4);
    --cmt-bg:rgba(210,153,34,.34); --cmt-on:rgba(210,153,34,.6); --cmt-line:#bb8009;
  }
}
* { box-sizing:border-box; }
body {
  margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;
}
a { color:var(--accent); }
header.top { border-bottom:1px solid var(--line); padding:22px 28px 18px; background:var(--soft); }
header.top .meta { color:var(--muted); font-size:13px; display:flex; gap:14px; flex-wrap:wrap; align-items:center; }
header.top h1 { margin:4px 0 10px; font-size:21px; line-height:1.3; font-weight:600; }
.stat { font-family:var(--mono); }
.stat .a { color:var(--plus); }
.stat .d { color:var(--minus); }
/* ---- annotation layer ---- */
mark.cmt { background:var(--cmt-bg); border-bottom:2px solid var(--cmt-line); color:inherit; padding:0; cursor:pointer; }
mark.cmt.on { background:var(--cmt-on); }
#cbar { position:absolute; z-index:40; display:none; }
#cbar button {
  font:500 12px/1 inherit; background:var(--fg); color:var(--bg); border:0; border-radius:6px;
  padding:7px 11px; cursor:pointer; box-shadow:0 2px 10px rgba(0,0,0,.25);
}
#cform {
  position:absolute; z-index:41; display:none; width:330px; background:var(--bg);
  border:1px solid var(--line); border-radius:10px; box-shadow:0 8px 30px rgba(0,0,0,.22); padding:12px;
}
#cform .q { font-size:12px; color:var(--muted); border-left:2px solid var(--cmt-line); padding-left:8px; margin-bottom:9px; max-height:52px; overflow:hidden; }
#cform textarea {
  width:100%; min-height:74px; resize:vertical; font:14px/1.5 inherit; color:var(--fg);
  background:var(--soft); border:1px solid var(--line); border-radius:6px; padding:8px;
}
#cform .kinds { display:flex; gap:6px; margin-top:9px; }
#cform .kinds button {
  flex:1; font:500 11.5px/1 inherit; padding:8px 4px; border-radius:6px; cursor:pointer;
  border:1px solid var(--line); background:var(--soft); color:var(--fg);
}
#cform .kinds button:hover:not(:disabled) { border-color:var(--accent); color:var(--accent); }
#cform .kinds button:disabled { opacity:.4; cursor:default; }
#cform .esc { margin-top:8px; font-size:11px; color:var(--muted); text-align:right; }
#ctoggle {
  position:fixed; right:20px; bottom:20px; z-index:30; border:1px solid var(--line);
  background:var(--bg); color:var(--fg); border-radius:22px; padding:9px 15px; cursor:pointer;
  font:500 13px/1 inherit; box-shadow:0 3px 14px rgba(0,0,0,.16);
}
#ctoggle .badge { color:var(--muted); margin-left:6px; font-family:var(--mono); font-size:11px; }
#cpanel {
  position:fixed; top:0; right:0; bottom:0; width:390px; max-width:92vw; z-index:35;
  background:var(--bg); border-left:1px solid var(--line); display:none; flex-direction:column;
  box-shadow:-6px 0 26px rgba(0,0,0,.14);
}
#cpanel.open { display:flex; }
#cpanel header { padding:16px 18px 12px; border-bottom:1px solid var(--line); display:flex; align-items:baseline; gap:10px; }
#cpanel header h3 { margin:0; font-size:15px; font-weight:600; }
#cpanel header .x { margin-left:auto; cursor:pointer; color:var(--muted); background:none; border:0; font-size:19px; line-height:1; }
#cpanel .list { overflow-y:auto; padding:8px 0 20px; flex:1; }
#cpanel .empty { color:var(--muted); font-size:13.5px; padding:22px 18px; }
.citem { padding:12px 18px; border-bottom:1px solid var(--line); cursor:pointer; }
.citem:hover { background:var(--soft); }
.citem.detached { opacity:.62; }
.citem .top { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
.citem .kind { font-size:10px; text-transform:uppercase; letter-spacing:.05em; border-radius:3px; padding:1px 6px; border:1px solid currentColor; }
.citem .kind.question { color:var(--accent); }
.citem .kind.remonter { color:var(--minus); }
.citem .kind.creuser { color:var(--muted); }
.citem .loc { font-family:var(--mono); font-size:10.5px; color:var(--muted); margin-left:auto; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.citem .quote { font-size:12px; color:var(--muted); border-left:2px solid var(--cmt-line); padding-left:8px; margin-bottom:6px; }
.citem .body { font-size:13.5px; white-space:pre-wrap; }
.citem .del { float:right; background:none; border:0; color:var(--muted); cursor:pointer; font-size:12px; padding:0 0 0 8px; }
#cpanel footer { border-top:1px solid var(--line); padding:11px 18px; display:flex; gap:8px; }
#cpanel footer button {
  flex:1; font:500 12px/1 inherit; padding:9px; border-radius:6px; cursor:pointer;
  border:1px solid var(--line); background:var(--soft); color:var(--fg);
}
#cpanel footer button:hover { border-color:var(--accent); color:var(--accent); }

.prtitle { color:var(--muted); font-style:italic; opacity:.75; }
.facts { margin:12px 0; }
.fact { border-bottom:1px solid var(--line); padding:7px 0; font-size:14px; }
.fact:last-child { border-bottom:0; }
.fact.flat { position:relative; padding-left:18px; }
.fact.flat .bullet { color:var(--muted); position:absolute; left:4px; }
.fact > summary { cursor:pointer; list-style:none; display:block; position:relative; padding-left:18px; }
.fact > summary::before { content:"›"; color:var(--muted); position:absolute; left:4px; top:0; transition:transform .12s; }
.fact[open] > summary::before { transform:rotate(90deg); transform-origin:center; }
.fact > summary::-webkit-details-marker { display:none; }
.factbody { padding:8px 0 4px 18px; max-width:70ch; }
.factbody p { margin:0 0 8px; }
pre.cmd {
  font-family:var(--mono); font-size:12px; background:var(--soft); border:1px solid var(--line);
  border-radius:6px; padding:8px 10px; margin:6px 0 0; overflow-x:auto; white-space:pre-wrap;
}
pre.cmd .pr { color:var(--muted); user-select:none; }
pre.cmd .out { color:var(--muted); }
.review .decision {
  display:inline-block; font-family:var(--mono); font-size:11px; letter-spacing:.04em;
  border:1px solid var(--line); border-radius:20px; padding:2px 10px; color:var(--muted);
}
.review .decision.CHANGES_REQUESTED { color:var(--minus); border-color:var(--minus); }
.review .decision.APPROVED { color:var(--plus); border-color:var(--plus); }
.revnote { max-width:70ch; margin:10px 0 0; color:var(--muted); font-size:14px; }
.threads { margin-top:12px; }
.thread { border-bottom:1px solid var(--line); padding:6px 0; font-size:13.5px; }
.thread:last-child { border-bottom:0; }
.thread > summary { cursor:pointer; list-style:none; }
.thread > summary::-webkit-details-marker { display:none; }
.thread > summary, .thread.flat { display:grid; grid-template-columns:auto auto 1fr; gap:10px; align-items:baseline; }
.thread .who { color:var(--muted); white-space:nowrap; }
.thread .where { font-family:var(--mono); font-size:11.5px; color:var(--accent); white-space:nowrap; }
.thread .lead { min-width:0; }
.thread .tag {
  font-size:10px; text-transform:uppercase; letter-spacing:.05em; border:1px solid var(--line);
  border-radius:3px; padding:0 5px; color:var(--muted); white-space:nowrap;
}
.thread .tag.brouillon { color:#9a6700; border-color:#9a6700; }
.threadbody .noreply { color:var(--muted); font-style:italic; margin:0 0 8px; font-size:13px; }
.threadbody { padding:8px 0 6px 0; max-width:78ch; }
.threadbody .full { margin-bottom:8px; }
.threadbody .reply { border-left:2px solid var(--line); padding:2px 0 2px 12px; margin:8px 0; color:var(--muted); }
.threadbody .reply .who { margin-right:8px; }
.summary { max-width:70ch; margin:12px 0 0; }
.summary p { margin:0 0 8px; }
.wrap { display:grid; grid-template-columns:250px minmax(0,1fr); align-items:start; }
nav { position:sticky; top:0; max-height:100vh; overflow-y:auto; padding:24px 12px 40px 24px; border-right:1px solid var(--line); }
nav ul { list-style:none; margin:0; padding:0; font-size:13px; }
nav li { margin:1px 0; }
nav li.sec { margin:16px 0 6px; text-transform:uppercase; letter-spacing:.06em; font-size:11px; color:var(--muted); font-weight:600; }
nav li.sec a { color:var(--muted); text-decoration:none; }
nav a { display:flex; gap:8px; padding:4px 8px; border-radius:6px; text-decoration:none; color:var(--fg); line-height:1.4; }
nav a .lbl { flex:1 1 auto; min-width:0; overflow-wrap:anywhere; }
nav .num { flex:0 0 auto; }
nav a:hover { background:var(--soft); }
nav a.active { background:var(--soft); color:var(--accent); font-weight:500; }
nav .num { color:var(--muted); font-family:var(--mono); font-size:11px; padding-top:2px; }
main { padding:28px 28px 25vh; min-width:0; max-width:1100px; }
main h2 { font-size:13px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); border-bottom:1px solid var(--line); padding-bottom:8px; margin:44px 0 4px; font-weight:600; }
main h2:first-child { margin-top:0; }
main h2 .sub { text-transform:none; letter-spacing:0; font-weight:400; margin-left:10px; }
.step { padding:22px 0; border-bottom:1px solid var(--line); }
.step:last-child { border-bottom:0; }
.step h3 { display:flex; gap:12px; align-items:baseline; font-size:17px; margin:0 0 8px; font-weight:600; }
.step h3 .num { font-family:var(--mono); font-size:12px; color:var(--muted); border:1px solid var(--line); border-radius:4px; padding:1px 6px; flex:none; }
.prose { max-width:70ch; }
code { font-family:var(--mono); font-size:.85em; background:var(--inline); border-radius:5px; padding:.15em .35em; word-break:break-word; }
h1 code, h3 code { font-size:.85em; background:none; padding:0; }
nav code { font-size:1em; background:none; padding:0; }
.prose p { margin:0 0 10px; }
.code { border:1px solid var(--line); border-radius:8px; overflow:hidden; margin:14px 0; }
.fh { display:flex; gap:10px; align-items:center; background:var(--soft); border-bottom:1px solid var(--line); padding:7px 12px; font-size:12px; }
.fh .path { font-family:var(--mono); }
.fh .note { color:var(--muted); }
.fh .gh { margin-left:auto; text-decoration:none; }
.fh .gh:hover { text-decoration:underline; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-family:var(--mono); font-size:12.5px; line-height:1.5; }
td { padding:0 4px; vertical-align:top; white-space:pre; }
td.ln { width:1%; min-width:42px; text-align:right; color:var(--muted); user-select:none; padding:0 8px; background:var(--soft); border-right:1px solid var(--line); }
td.sg { width:1%; padding:0 4px 0 8px; user-select:none; color:var(--muted); }
td.cd { width:100%; padding-right:16px; }
tr.add td.cd, tr.add td.sg { background:var(--add-bg); }
tr.add td.ln { background:var(--add-ln); }
tr.del td.cd, tr.del td.sg { background:var(--del-bg); }
tr.del td.ln { background:var(--del-ln); }
tr.hunk td { background:var(--hunk-bg); color:var(--hunk); font-size:11.5px; padding:3px 4px; }
tr.meta td { color:var(--muted); }
.src tr.hl td.cd { background:var(--hl); }
.src tr.elide td { color:var(--muted); text-align:center; height:14px; }
.src tr.elide td.ln { text-align:center; }
.ctxblock .fh { background:transparent; }
blockquote { margin:12px 0; padding:10px 16px; border-left:3px solid var(--line); color:var(--muted); max-width:70ch; }
blockquote footer { margin-top:8px; font-size:13px; }
details summary { cursor:pointer; color:var(--muted); font-size:14px; }
ul.noise { list-style:none; padding:10px 0 0; margin:0; font-size:13px; }
ul.noise li { display:flex; gap:14px; padding:2px 0; }
ul.noise .path { font-family:var(--mono); }
ul.noise .dl { color:var(--muted); font-family:var(--mono); font-size:12px; }
ul.noise .n { color:var(--muted); font-size:12px; }
@media (max-width:900px) {
  .wrap { grid-template-columns:1fr; }
  nav { position:static; max-height:none; border-right:0; border-bottom:1px solid var(--line); }
}
"""

JS = """
const links = [...document.querySelectorAll('nav a[href^="#"]')];
const byId = new Map(links.map(a => [a.getAttribute('href').slice(1), a]));
const io = new IntersectionObserver(entries => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    links.forEach(a => a.classList.remove('active'));
    byId.get(e.target.id)?.classList.add('active');
  }
}, { rootMargin: '0px 0px -75% 0px', threshold: 0 });
document.querySelectorAll('section.step[id]').forEach(s => io.observe(s));
"""


COMMENTS_JS = r"""
/* ---------- annotation layer: select text, attach a comment ---------------
   Anchors are semantic (a content hash for prose/facts, path:line for code),
   so a comment survives a re-render of the brief and can be handed to GitHub
   as an inline comment without re-deriving anything. Stored in localStorage. */
(function () {
  const meta = JSON.parse(document.getElementById('brief-meta').textContent);
  const KEY = 'pr-brief:' + meta.repo + '#' + meta.number + ':comments';
  const KINDS = { question: 'Question', remonter: 'À remonter', creuser: 'À creuser' };

  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; } };
  const save = (c) => { try { localStorage.setItem(KEY, JSON.stringify(c)); } catch (e) {} };
  let comments = load();
  let pending = null;

  const bar = document.getElementById('cbar');
  const form = document.getElementById('cform');
  const panel = document.getElementById('cpanel');
  const list = panel.querySelector('.list');
  const badge = document.querySelector('#ctoggle .badge');

  /* -- text nodes of an element, in order -- */
  function textNodes(root) {
    const out = [], w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n; while ((n = w.nextNode())) out.push(n);
    return out;
  }

  /* -- wrap [from,to) of an anchor's text content in <mark> -- */
  function paint(el, quote, id) {
    const nodes = textNodes(el);
    const full = nodes.map(n => n.nodeValue).join('');
    const at = full.indexOf(quote);
    if (at < 0) return false;
    const to = at + quote.length;
    let pos = 0;
    for (const node of nodes.slice()) {
      const len = node.nodeValue.length, start = pos, end = pos + len;
      pos = end;
      if (end <= at || start >= to) continue;
      const a = Math.max(at - start, 0), b = Math.min(to - start, len);
      const range = document.createRange();
      range.setStart(node, a); range.setEnd(node, b);
      const mark = document.createElement('mark');
      mark.className = 'cmt'; mark.dataset.id = id;
      try { range.surroundContents(mark); } catch (e) { return false; }
    }
    return true;
  }

  function unpaint(id) {
    document.querySelectorAll('mark.cmt[data-id="' + id + '"]').forEach(m => {
      const parent = m.parentNode;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m); parent.normalize();
    });
  }

  function repaint() {
    comments.forEach(c => {
      if (document.querySelector('mark.cmt[data-id="' + c.id + '"]')) return;
      const el = document.querySelector('[data-anchor="' + CSS.escape(c.anchor) + '"]');
      c.detached = !(el && paint(el, c.quote, c.id));
    });
  }

  /* -- selection -> anchor -- */
  function anchorOf(node) {
    let el = node.nodeType === 3 ? node.parentElement : node;
    while (el && !el.dataset.anchor) el = el.parentElement;
    return el;
  }

  document.addEventListener('mouseup', (ev) => {
    if (form.contains(ev.target) || panel.contains(ev.target) || bar.contains(ev.target)) return;
    setTimeout(() => {
      const sel = window.getSelection();
      const quote = sel && !sel.isCollapsed ? sel.toString().trim() : '';
      if (!quote) { bar.style.display = 'none'; return; }
      const el = anchorOf(sel.anchorNode);
      if (!el || el !== anchorOf(sel.focusNode)) { bar.style.display = 'none'; return; }
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      pending = {
        anchor: el.dataset.anchor, quote: quote,
        path: el.dataset.path || null, line: el.dataset.line ? +el.dataset.line : null,
        side: el.dataset.side || null,
      };
      bar.style.display = 'block';
      bar.style.left = (window.scrollX + rect.left) + 'px';
      bar.style.top = (window.scrollY + rect.bottom + 7) + 'px';
    }, 0);
  });

  bar.querySelector('button').onclick = () => {
    if (!pending) return;
    bar.style.display = 'none';
    form.style.left = bar.style.left; form.style.top = bar.style.top;
    form.querySelector('.q').textContent = pending.quote;
    const ta = form.querySelector('textarea');
    ta.value = '';
    form.style.display = 'block';
    ta.focus();
    syncKinds();
  };

  const syncKinds = () => {
    const empty = !form.querySelector('textarea').value.trim();
    form.querySelectorAll('.kinds button').forEach(b => { b.disabled = empty; });
  };
  form.querySelector('textarea').addEventListener('input', syncKinds);

  form.querySelectorAll('.kinds button').forEach(button => {
    button.onclick = () => {
      const text = form.querySelector('textarea').value.trim();
      if (!text || !pending) return;
      const c = Object.assign({}, pending, {
        id: 'c' + Date.now().toString(36),
        kind: button.dataset.kind, body: text, createdAt: new Date().toISOString(),
      });
      comments.push(c); save(comments);
      paint(document.querySelector('[data-anchor="' + CSS.escape(c.anchor) + '"]'), c.quote, c.id);
      closeForm(); render();
    };
  });

  function closeForm() {
    form.style.display = 'none'; bar.style.display = 'none';
    pending = null; window.getSelection().removeAllRanges();
  }
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeForm(); });
  document.addEventListener('mousedown', e => {
    if (form.style.display === 'block' && !form.contains(e.target)) closeForm();
  });

  /* -- panel -- */
  function locOf(c) {
    if (c.path) return c.path.split('/').pop() + ':' + c.line;
    return c.detached ? 'détaché' : 'texte';
  }

  function render() {
    badge.textContent = comments.length || '';
    if (!comments.length) {
      list.innerHTML = '<p class="empty">Sélectionne du texte dans la page, puis clique sur « Commenter ».</p>';
      return;
    }
    list.innerHTML = comments.map(c =>
      '<div class="citem' + (c.detached ? ' detached' : '') + '" data-id="' + c.id + '">' +
        '<div class="top"><span class="kind ' + c.kind + '">' + KINDS[c.kind] + '</span>' +
          '<span class="loc">' + locOf(c) + '</span>' +
          '<button class="del" title="Supprimer">×</button></div>' +
        '<div class="quote">' + escapeHtml(c.quote.slice(0, 160)) + '</div>' +
        '<div class="body">' + escapeHtml(c.body) + '</div>' +
      '</div>').join('');
  }

  const escapeHtml = (t) => t.replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));

  list.onclick = (e) => {
    const item = e.target.closest('.citem'); if (!item) return;
    const id = item.dataset.id;
    if (e.target.classList.contains('del')) {
      comments = comments.filter(c => c.id !== id); save(comments); unpaint(id); render(); return;
    }
    const mark = document.querySelector('mark.cmt[data-id="' + id + '"]');
    if (mark) {
      mark.scrollIntoView({ behavior: 'smooth', block: 'center' });
      document.querySelectorAll('mark.cmt.on').forEach(m => m.classList.remove('on'));
      document.querySelectorAll('mark.cmt[data-id="' + id + '"]').forEach(m => m.classList.add('on'));
    }
  };

  document.addEventListener('click', (e) => {
    const mark = e.target.closest('mark.cmt'); if (!mark) return;
    panel.classList.add('open');
    const item = list.querySelector('.citem[data-id="' + mark.dataset.id + '"]');
    if (item) item.scrollIntoView({ block: 'center' });
  });

  document.getElementById('ctoggle').onclick = () => panel.classList.toggle('open');
  panel.querySelector('.x').onclick = () => panel.classList.remove('open');

  /* -- exits: clipboard for Claude Code, JSON for the record -- */
  function asMarkdown() {
    const order = ['remonter', 'question', 'creuser'];
    let out = '# Commentaires — ' + meta.repo + '#' + meta.number + '\n\n> ' + meta.headline + '\n';
    order.forEach(kind => {
      const group = comments.filter(c => c.kind === kind);
      if (!group.length) return;
      out += '\n## ' + KINDS[kind] + '\n';
      group.forEach(c => {
        out += '\n### ' + (c.path ? c.path + ':' + c.line : 'texte du brief') + '\n';
        out += '> ' + c.quote.replace(/\n/g, '\n> ') + '\n\n' + c.body + '\n';
      });
    });
    return out;
  }

  panel.querySelector('.copy').onclick = (e) => {
    navigator.clipboard.writeText(asMarkdown()).then(() => {
      e.target.textContent = 'Copié'; setTimeout(() => { e.target.textContent = 'Copier pour Claude'; }, 1400);
    });
  };
  panel.querySelector('.dl').onclick = () => {
    const blob = new Blob([JSON.stringify({ repo: meta.repo, number: meta.number, comments }, null, 2)],
                          { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = meta.repo.split('/').pop() + '-' + meta.number + '.comments.json';
    a.click(); URL.revokeObjectURL(a.href);
  };

  repaint(); render();
})();
"""

MARKUP = """<div id="cbar"><button>Commenter</button></div>
<div id="cform"><div class="q"></div><textarea placeholder="Ta note…"></textarea>
<div class="kinds"><button data-kind="question">Question</button>
<button data-kind="remonter">À remonter</button>
<button data-kind="creuser">À creuser</button></div>
<div class="esc">Échap pour annuler</div></div>
<button id="ctoggle">Commentaires<span class="badge"></span></button>
<aside id="cpanel"><header><h3>Commentaires</h3><button class="x">&times;</button></header>
<div class="list"></div>
<footer><button class="copy">Copier pour Claude</button><button class="dl">JSON</button></footer></aside>"""


def build(data):
    brief = Brief(data)
    summary = "".join(
        f'<p data-anchor="{anchor_for("p", para)}">{rich(para)}</p>'
        for para in as_paragraphs(data.get("summary"))
    )
    impact = brief.render_steps("impact", "impact")
    impact_heading = (
        '<h2 id="impact">Rayon d\'impact'
        '<span class="sub">code touché qui n\'est pas dans le diff</span></h2>'
        if impact
        else ""
    )
    headline = data.get("headline") or data.get("title")
    short_repo = brief.repo.split("/")[-1]
    head = (
        "<!doctype html>\n<html lang=\"fr\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(short_repo)}#{esc(brief.number)} — {esc(headline)}</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n"
    )
    body = (
        '<header class="top"><div class="meta">'
        f'<a href="{esc(brief.pr_url)}" target="_blank" rel="noreferrer">{esc(brief.repo)}#{esc(brief.number)}</a>'
        f'<span>{esc(data.get("author"))}</span>'
        f'<span class="stat"><span class="a">+{int(data.get("additions") or 0)}</span> '
        f'<span class="d">−{int(data.get("deletions") or 0)}</span></span>'
        f'<span class="prtitle">titre PR : {esc(data.get("title"))}</span></div>'
        f'<h1>{rich(headline)}</h1>'
        f'<div class="summary">{summary}</div></header>'
        f'<div class="wrap"><nav><ul>{brief.render_toc()}</ul></nav><main>'
        '<h2 id="parcours">Parcours de lecture</h2>'
        f'{brief.render_steps("steps", "etape")}'
        f"{impact_heading}{impact}{brief.render_my_feedback()}{brief.render_noise()}"
        "</main></div>"
    )
    meta_json = json.dumps({"repo": brief.repo, "number": brief.number,
                            "headline": headline, "url": brief.pr_url}, ensure_ascii=False)
    return (head + body + MARKUP
            + f'<script type="application/json" id="brief-meta">{meta_json}</script>'
            + f"<script>{JS}</script>\n<script>{COMMENTS_JS}</script>\n</body>\n</html>\n")


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: render.py <brief.json> <out.html>")
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
    with open(sys.argv[2], "w", encoding="utf-8") as handle:
        handle.write(build(data))
    print(sys.argv[2])


if __name__ == "__main__":
    main()
