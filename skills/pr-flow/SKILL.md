---
name: pr-flow
description: Draw the mechanism of a PR as an interactive flow page — triggers, the components they reach, what fans out where — with every box opening the diff hunks and out-of-diff code it stands for. Read-only, no judgement. Use when a PR is hard to hold in the head because the change is spread across callbacks, jobs, services or entry points, and a diagram would beat a reading path; complements pr-brief.
---

# PR Flow Skill

Goal: make me **see** the mechanism of a PR — who triggers what, through which components, what
fans out where — when the diff is scattered across callbacks, jobs, services and entry points and
a linear reading path (`pr-brief`) isn't enough. Reviewing is a separate pass; this page builds the
model I review against.

Output: **one self-contained HTML file per PR**, opened in my browser: a left-to-right SVG diagram
where **every box carries three gestures**. Clicking its body opens a modal with the code it
stands for — the diff hunks, verbatim, and the out-of-diff excerpts with the lines that matter
highlighted. The target in its corner **isolates its subflow**: everything that leads to it and
everything it reaches, transitively, the rest dropping to light grey. The cross next to it
**removes the box**, and with it every box that no longer has a reason to be drawn — one that used
to have incoming edges and has none left, or used to have outgoing ones and has none left, the
removal cascading until it settles; origins and effects keep the side they never had. **A box can
also be dragged** to pull apart edge labels that sit on top of each other; its wires follow. The
diagram sits in a full-width canvas at 1:1 (the text keeps its size whatever the diagram's width),
panned by dragging the background and zoomed with Ctrl + wheel or the toolbar, and a
**Réinitialiser** button puts every box back, clears the isolation and returns to 100 %. The page
embeds the repository's code verbatim: it stays a local file, never published or uploaded.

## The rules carried over from `pr-brief` — all of them apply

- **Zero round-trip.** Every box's modal embeds the code. Never "voir `x.rb:12`" — embed `x.rb:12`.
- **Describe, never evaluate.** Facts ("aucun test sur `Account` dans ce job", "sans plafond"),
  never verdicts ("risque", "il faudrait", "attention"). If a fact looks alarming, state it and stop.
- **French prose, source-code names verbatim**, backticks on greppable identifiers in HTML parts
  (`intro`, `headline`, table cells, `reading`) — not in SVG `title`/`lines`, which are plain text.
- **Title and description of the PR are not evidence.** Derive everything from the branch.
- **Strictly read-only** on GitHub. Discovery, fetch, reading — nothing else.
- **Branch mechanics**: same as `pr-brief` — `gh pr view/diff`, `git fetch origin refs/pull/<N>/head:refs/pr-brief/<N>`
  (named ref, never `FETCH_HEAD`), read with `git show <ref>:<path>` / `git grep <ref>`, temp files
  suffixed with `-<N>`, blobless clone into the scratchpad when the repo isn't local, delete the ref
  when done. **Reuse `~/.claude/pr-briefs/<repo>-<N>.json` when it exists**: its `diff`/`context`/
  `facts` blocks have the exact shape this skill uses — copy them, don't rebuild them.

## What to draw

Draw as the engineer who has to live with the code, not as a decorator.

- **Depict the mechanism, not its name.** Columns are the stages a signal crosses (typically:
  *point d'entrée → écriture / événement → composant qui décide → effet*), boxes are the concrete
  things at each stage, edges are labelled with what actually travels (`perform_later`,
  `user_ids: [id]`, `× N users`). A box named after a concept ("notification") says less than the
  method, callback or table it really is.
- **Include what the diff doesn't show.** The paths that reach the new code by ricochet
  (an existing `after_commit`, a job that calls `save!`, a controller with nested attributes) and
  the paths that *don't* reach it (`insert_all`, imports) — the second kind drawn with the
  `none` edge, so "rien n'est enfilé" is visible, not implied. This is the part I cannot build
  from GitHub.
- **Where the work concentrates gets a box of its own**: a `note`-toned box for "aucun service
  commun : deux requêtes, deux jeux de guards", a `band` for shared infrastructure (queue config).
  Both open modals like any other box — the note shows the two pieces of code side by side.
- **Match complexity to the stakes.** A one-hop PR is three boxes; don't inventory the system.
  Grep before drawing: every box must correspond to code you have opened.
- **Label the arrows.** An unlabelled arrow is "related somehow". Keep labels short: the gap
  between two columns widens to the longest label that crosses it, so a 40-character label costs
  250 px of width. A `none` edge's label is drawn inside its box, prefixed with ∅.
- **Titles and lines wrap** to the box width and the box grows; still, one idea per line.

Tones trace paths: `a`/`b`/`c` for the distinct trigger paths (one hue per path, consistently
from entry to effect), `off` for boxes that lead nowhere, `note`, `band`, `plain`. Edge kinds:
`solid` (direct: callback, method call), `dash` (explicit call that replaces a bypassed
callback), `none` (∅ — nothing happens; add the `label` saying so).

## What goes in each modal

One `intro` (1–3 descriptive sentences), then blocks, most load-bearing first:

- the **diff hunks** of the files the box stands for — verbatim, headers recomputed if trimmed;
- the **out-of-diff excerpts** that make the box true — the enclosing method, whole, with the
  decisive lines in `highlight` (the `save!`, the `RETURNING`, the guard that is or isn't there);
- **facts** with the command that establishes them and its *real* output, produced by `B.ran`
  (`git grep … → aucun résultat`, a generated SQL). `Blocks.fact` with a pasted output only for
  a scenario run in a console. Never invent an output.

Build every block with `blocks.py`, which lives in the sibling skill: `<skill-dir>/../pr-brief/blocks.py`
(`<skill-dir>` is the directory this `SKILL.md` was loaded from). Write a builder script in the
scratchpad, run it from the clone that holds the ref — the same pattern as `pr-brief`, step 3:

```python
import sys; sys.path.insert(0, "<skill-dir>/../pr-brief")
from blocks import Blocks
B = Blocks(ref="refs/pr-brief/<N>", diff_path="<scratch>/pr-<N>.diff")
B.diff("app/models/order_refund.rb")                       # all hunks
B.diff("app/views/x.html.erb", trim=(86, 105))             # new file, trimmed, header recomputed
B.ctx("app/jobs/foo_job.rb", [(26, 47)], highlight=[31, 46], note="save! → callback")
B.ctx("app/models/user.rb", [(73, 76), (290, 297)], highlight=[76, 296])   # segments
B.ran("Aucun test sur `Account` dans ce job.", "git grep -n Account refs/pr-brief/<N> -- spec/jobs")
```

A fact's `output` is never typed: `B.ran` executes the command and embeds what it printed.

## The JSON spec — `~/.claude/pr-flows/<repo-short>-<number>.json`

`mkdir -p` the directory first, and keep the file (see the end of this page). The shape below
is shown as JSON; the builder script writes it as a Python dict, so `true`/`false`/`null`
become `True`/`False`/`None` there.

```jsonc
{
  "repo": "Owner/repo", "number": 250, "url": "https://github.com/…/pull/250",
  "head_sha": "…",                         // required: GitHub links in modals point at it
  "title": "Flow notify_order_shipped",          // page name — short noun phrase
  "headline": "Qui déclenche `x`, et par où ça passe",
  "lede": ["1–2 phrases : comment lire le schéma"],
  "caption": "la phrase que le schéma démontre",
  "aria": "description textuelle du schéma",

  "tones": { "a": {"label": "chemin OrderShipment"}, "b": {"label": "chemin OrderRefund"} },
  "legend": { "solid": "plein = after_commit", "dash": "tireté = appel explicite", "none": "aucune notification" },

  "columns": [ {"id": "entry", "head": "Point d'entrée", "width": 310},
               {"id": "event", "head": "Écriture · événement", "width": 250},
               {"id": "job",   "head": "Job décideur", "width": 300},
               {"id": "effect","head": "Mails", "width": 220} ],

  "nodes": [                               // stacked top-down per column, in array order
    { "id": "gc", "col": "entry", "tone": "plain",
      "title": "OrdersController#update",                     // plain text (SVG)
      "lines": [ {"text": "backoffice · shipments_attributes", "style": "sub"} ],
      "intro": "phrase descriptive (HTML, backticks ok)",
      "blocks": [ /* diff | context | facts — same shape as pr-brief */ ] },
    { "id": "shp", "col": "event", "tone": "a", "title": "OrderShipment",
      "lines": [ {"text": "after_commit on: :create", "style": "sub"},
                 {"text": "→ order_id, shipment_id", "style": "lab"} ],
      "blocks": [ … ] },
    { "id": "note", "col": "job", "tone": "note", "sans": true, "gap": 18,
      "title": "Aucun service commun entre les deux jobs",
      "lines": [ {"text": "…", "style": "sans"} ], "blocks": [ … ] },
    { "id": "sq", "col": "*", "tone": "band", "sans": true, "title": "SolidQueue · config/queue.yml",
      "lines": [ {"text": "…", "style": "sans"} ], "blocks": [ … ] }
  ],
  "edges": [
    { "from": "gc",  "to": "shp", "tone": "a", "kind": "solid" },
    { "from": "shp", "to": "gjob", "tone": "a", "kind": "solid", "label": "perform_later" },
    { "from": "bulk","to": "ujob", "tone": "b", "kind": "dash",  "label": "perform_later" },
    { "from": "imp", "kind": "none", "label": "pas de callback — rien n'est enfilé" },
    { "from": "gjob","to": "mdj",  "tone": "a", "label": "× N users", "label_at": "vertical" }
  ],

  "table": { "title": "Par déclencheur : ce qui est enfilé, et ce qui borne N",
             "head": ["Déclencheur", "Écriture", "Job enfilé", "Argument", "Effet", "Borne de N"],
             "rows": [ {"node": "gc", "tone": "a", "cells": ["`OrdersController#update`", "…"]} ] },
  "reading": ["2–3 puces : ce que le schéma montre — faits, pas verdicts"]
}
```

Node fields: `y` (absolute, overrides stacking), `gap` (extra space before), `span` (columns to
the right to cover), `height`, `cue` (`diff`/`code`, auto), `modal_title`, `sans` (sans-serif
title). Line styles: `code` (default mono), `sub` (muted), `lab` (tone-coloured), `sans`
(muted sans), `text` (sans, ink). Edge fields: `label_at` (`start` default / `end` / `vertical`
for a rotated label on the elbow), `to_y`, `elbow_x`. A node without `blocks`/`intro` is drawn
but not clickable — avoid that: a box I cannot open is a pointer.

The renderer stacks nodes per column in order, sizes boxes from their line count, routes edges
(straight when aligned, elbow otherwise, vertical inside a column), builds the legend from the
tones and kinds actually used, and refuses an edge or table row that names an unknown node.

## Render and open

```
python3 <skill-dir>/render.py \
  ~/.claude/pr-flows/<repo-short>-<number>.json \
  ~/.claude/pr-flows/<repo-short>-<number>.html --open
```

`render.py` needs nothing beyond the Python standard library. `--open` shows the page in the
default browser on Linux and macOS alike — never call `xdg-open` or `open` yourself.

Then a one-line report in the chat: `repo#number — titre — chemin du fichier`. Everything else
lives in the page.

Keep the JSON: re-rendering after a renderer fix or a new node must not redo the analysis. Delete
`refs/pr-brief/<N>` and any worktree when done; leave the working tree as you found it.
