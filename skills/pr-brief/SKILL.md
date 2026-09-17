---
name: pr-brief
description: Build a comprehension brief for one or several PRs as a local HTML page — the change explained in reading order, with every relevant diff hunk and out-of-diff excerpt embedded inline. Read-only, no judgement, no comments. Use before reviewing PRs, when I need to load a PR's context without challenging it yet.
---

# PR Brief Skill

Goal: get me to the point where I **understand** a PR, without reviewing it. Challenging the code
is a separate pass I run later (`review-requested-prs`), when I'm ready.

Output: **one self-contained HTML file per PR**, opened in my browser. Not a chat message.

## The acceptance criterion — zero round-trip

The previous version of this brief handed me homework ("va voir `column.rb:18`"). Every pointer is a
round-trip, and round-trips are exactly what I was already paying for on GitHub.

**If reading the page requires me to open my editor or GitHub, the page failed.** Every piece of
code the brief refers to must be embedded in the page. Never write "voir `x.rb:12`" — embed the code
of `x.rb:12`. No exception.

## Absolute rule — describe, never evaluate

A brief describes; it never judges. That separation is the point: if the brief criticises, I read
its criticism instead of building my own model of the code.

- **Facts, yes** — "aucun spec ne couvre `Cart#empty?`", "l'autorisation est vérifiée dans le
  serializer et plus dans le controller", "la migration tourne sans backfill sur 400k lignes".
- **Verdicts, no** — "il faudrait un spec", "attention à", "risque de", "bon réflexe",
  "il aurait mieux valu", "de façon élégante".

If a fact looks alarming, state the fact and stop. Drawing the conclusion is my job.

## Language — French prose, source-code names

The brief is written in French (edit this paragraph to pick another language), but every
entity represented in the source code keeps its **source-code name, verbatim**: `order`, `invoice`,
`line item`, `account admin`, `super-admin`. Never translate them — not "commande" for `order`,
not "facture" for `invoice`, not "administrateur de compte" for an account admin. The prose carries
the grammar and the connective tissue; the names stay greppable.

**Inline code.** In prose, titles and the summary, wrap **code identifiers** in backticks —
class and module names, methods, attributes, params keys, controller actions, file paths,
kwargs: `Orders::CreateService`, `invoiced_at`, `line_items=`, `on: :create`,
`order[line_items_attributes][]`. The renderer turns them into inline `<code>`.
Do **not** backtick domain vocabulary used as an ordinary noun — order, invoice, customer, account,
admin in running prose stay plain, otherwise every sentence turns into visual noise.
The line is: an identifier you could grep for gets backticks, a concept you are naming does not.

## Strictly read-only

No write to GitHub, ever: no `POST`/`PUT`/`DELETE`, no `gh pr review`, no comment, no reaction,
no label. Discovery, checkout and reading only. `gh` is authenticated.

---

## Step 1 — Choose the PRs

If the invocation names a PR (number, `repo#number`, or URL), brief that one and go to step 2.

Otherwise:
- `gh search prs --review-requested=@me --state=open --json number,title,repository,url,isDraft,updatedAt --limit 100`
- Drop drafts (`isDraft == true`). If the result is empty, tell me and stop.
- Print a recap table `Repo | # | Titre | Mis à jour`, then let me narrow it down:
  ≤4 PRs → **AskUserQuestion** multiSelect, one option per PR (default = all, I'm only deselecting);
  >4 PRs → keep the table and ask me which numbers to skip (default: brief them all).

## Step 2 — Read the branch, not just the diff

For each PR:
- `gh pr view <N> --repo <REPO> --json title,author,baseRefName,headRefName,files,additions,deletions,url`
- head sha: `gh pr view <N> --repo <REPO> --json commits --jq '.commits[-1].oid'`
- `gh pr diff <N> --repo <REPO>`
- Fetch into a **named ref**, never `FETCH_HEAD`:
  `git fetch origin refs/pull/<N>/head:refs/pr-brief/<N>`. `FETCH_HEAD` is a single file per repo,
  so concurrent briefs in the same repo overwrite each other's — the named ref is what makes
  parallel PRs safe. Then read the branch **without checking it out**:
  `git show refs/pr-brief/<N>:<path>` and `git grep -n <pattern> refs/pr-brief/<N> -- <source dirs>`.
  **Derive the source dirs from the repo, never assume `app lib spec`** — a front-end repo keeps its
  code in `src`, and its load-bearing files (`Dockerfile`, CI config, `package.json`, entrypoints)
  sit at the root, outside any directory. Look at the tree before you grep.
  Nothing is written to the working tree. Delete the ref when done:
  `git update-ref -d refs/pr-brief/<N>`.
- **Look for a local clone before cloning.** Match on the remote, not on the directory name — a
  clone can sit under a parent folder, and a same-named clone can point at another owner. Search
  two levels under the projects root (`~/projects` unless the user's setup says otherwise):
  ```
  for d in ~/projects/*/ ~/projects/*/*/; do
    [ -d "$d.git" ] && git -C "$d" remote get-url origin 2>/dev/null | grep -qiE '[:/]<OWNER>/<REPO>(\.git)?$' && echo "$d"
  done
  ```
  Several hits (a mirror, an `_integrated` copy) → take the first plain clone. Reading through a
  named ref touches no working tree, so a dirty clone is fine. Only when nothing matches, clone
  into the scratchpad, never into the user's project tree:
  ```
  git clone --filter=blob:none git@github.com:<OWNER>/<REPO>.git <REPO>-<N>
  cd <REPO>-<N> && git fetch origin refs/pull/<N>/head:refs/pr-brief/<N> && git checkout --detach refs/pr-brief/<N>
  ```
  The clone itself is cheap (a few seconds). **Do not run `git grep` against a blobless clone** —
  it refetches blob by blob, one network round-trip per file, and times out. The `checkout` backfills
  every blob in a single batch, after which plain `grep -rn` is instant.
- **Every temp file you write must carry the PR number**: `<something>-<N>.diff`, never `diff.txt`.
  The named ref protects the git side; nothing protects the scratchpad, and a sibling brief running
  concurrently will overwrite a generic name. Reading another PR's diff produces no error, just a
  silently wrong brief.
- Only fall back to `gh pr checkout <N>` when a tool genuinely needs the files on disk — and never
  in a repo whose working tree is dirty.
- Read the changed files **and the code around them** — callers, scopes, authorizations, serializers,
  migrations, the specs that cover them. A brief built from the diff alone is worthless: I can read a
  diff myself. The value is in what the diff doesn't show.

**The PR title and description are not evidence.** Neither is chosen by the developer in any
meaningful sense: the title often comes from a Jira ticket that is itself badly named, the
description is often generated on a first iteration and never adjusted as the code changed. Both
describe a past state at best.

So: never read them as a claim, never use them as a baseline, and never write "ce que le titre ne
dit pas" or "contrairement à la description" — that anchors the brief on an unreliable reference.
Derive everything from the code as it stands on the branch right now. The title survives on the
page only as an identifier, in the meta line next to `repo#number`; the `headline` you write is what
the reader sees first, and it must be true of the code being reviewed.

Several PRs → one subagent per PR. Reading through a named ref needs no worktree; only use
`isolation: "worktree"` if a PR forced a real checkout, and clean it up afterwards
(`git worktree remove --force`, `git worktree prune`). Each subagent writes its own JSON + HTML and
returns the output path. Leave the working tree as you found it.

## Step 3 — Write the brief as JSON

Write `~/.claude/pr-briefs/<repo-short>-<number>.json` (`mkdir -p` the directory first; keep the
file: it allows a re-render without redoing the analysis).

**Build it with a script, never by hand.** Write `<scratch>/build-<N>.py`, run it from the clone
that holds the ref, and let `blocks.py` (next to this `SKILL.md`) produce every block: hunks come
from the saved diff, excerpts from `git show`, fact outputs from commands it executes itself.
Typing a hunk, a line number or a command output by hand is how a brief lies.

```python
import sys; sys.path.insert(0, "<skill-dir>")
from blocks import Blocks
B = Blocks(ref="refs/pr-brief/<N>", diff_path="<scratch>/pr-<N>.diff")

B.diff("app/models/order.rb")                      # every hunk of the file, verbatim
B.diff("app/models/order.rb", hunks=[0])           # selected hunks
B.diff("app/views/x.html.erb", trim=(31, 71))      # new-file hunk cut to added lines 31..71, header recomputed
B.ctx("app/jobs/foo_job.rb", [(26, 47)], highlight=[31, 46], note="save! → callback")
B.ctx("app/models/user.rb", [(73, 76), (290, 297)], highlight=[76])   # two segments, one block
B.ran("Aucun spec ne nomme `Foo`.", "git grep -n Foo refs/pr-brief/<N> -- spec")   # runs the command
Blocks.facts([ ... ])                              # wraps fact items into a facts block
```

Then `json.dump` the dict below to the output path. Shape:

```jsonc
{
  "repo": "Owner/repo",
  "number": 4261,
  "title": "…",                    // the PR title — identifier only, never a claim
  "headline": "…",                 // YOUR one-line statement of what this code does; the page's h1
  "author": "login",
  "url": "https://github.com/Owner/repo/pull/4261",
  "additions": 142, "deletions": 18,
  "head_sha": "…",                 // required for the out-of-diff links
  "summary": ["phrase 1", "phrase 2"],

  "steps": [                       // the reading path — 3 to 6 steps, in comprehension order
    {
      "title": "Ce qui est ajouté en base",
      "prose": ["1 à 3 phrases descriptives"],
      "blocks": [
        { "kind": "diff",
          "path": "db/migrate/2026…_add_label.rb",
          "hunks": ["@@ -14,6 +14,8 @@ optional context\n unchanged line\n+added line\n-removed line"] },
        { "kind": "facts",         // things that are NOT there — see below
          "items": [
            { "text": "`LineItem` ne porte aucune validation.",
              "detail": ["complément facultatif"],
              "command": "git show <head>:app/models/line_item.rb",
              "output": "…" }
          ] },
        { "kind": "context",       // code NOT in the diff
          "path": "app/exports/invoice_export.rb",
          "start_line": 18,        // line number of the excerpt's first line
          "highlight": [22],       // the line(s) that matter
          "code": "def rows\n  …\nend" }
      ]
    }
  ],

  "impact": [                      // same shape — code touched outside the diff
    { "title": "…", "prose": ["…"], "blocks": [ … ] }
  ],

  "my_feedback": {                 // omit the key entirely when I have no thread on this PR
    "note": "…",
    "threads": [{
      "path": "app/…/x.rb", "line": 12,
      "body": "ce que j'ai écrit",
      "tags": ["brouillon"],       // "brouillon" = PENDING, never sent; "caduc" = isOutdated
      "replies": [{ "author": "login", "body": "…" }],
      "blocks": [ … ]              // the code AT THAT ANCHOR as it stands on the branch today
    }]
  },

  "noise": [{ "path": "config/locales/fr.yml", "additions": 3, "deletions": 0 }]
}
```

### What goes in each section

**headline** — one line, your own, stating what this code does. It replaces the PR title as the
page's heading, so it carries the weight the title cannot: it must be true of the branch as it
stands. Name the mechanism, not the intent.

**summary** — two or three sentences: what this change does, read from the code.

**steps** — the reading path, in the order that builds understanding, entry point first. GitHub
sorts files alphabetically, the worst possible order. Three to six steps; each step is one idea, its
descriptive framing, and the code that shows it. Frame with "ce qui est accepté en entrée", "ce que
ça renvoie maintenant", not with an opinion.

**impact** — whatever the change reaches that is **not in the diff**. On a Rails backend that is
usually callers of the modified methods, overridden behaviour, shared state, background jobs,
migrations running against existing rows, endpoints whose response shape moves, feature flags.
Elsewhere it is something else entirely: build-time and CI wiring (a `--build-arg` the pipeline
never passes, a script nothing calls), bundler/module resolution, generated assets, deploy config.
**The categories are examples, not a checklist** — ask what this code reaches, in this repo. Embed the code as `context`
blocks, never as a reference. This is the section I cannot build from the GitHub UI.

**my_feedback** — **only my own threads.** Other reviewers' change requests are deliberately out
of scope: I read those on GitHub when I get there. This skill builds context on the *proposal*
before I go and challenge it; the one exception is what **I** already said, because re-loading my
own state is exactly the cost this brief exists to remove.

Find them with `gh api graphql` on `reviewThreads`, keeping the threads whose **first comment author
is me** (`gh api user --jq .login`). Include `PENDING` ones — drafts I never submitted, visible only
to me — and tag them `brouillon`; tag `caduc` what GitHub marks `isOutdated`. Carry every reply.

Never state whether a point was addressed: that is a judgement, and `isOutdated` only means the
anchored lines moved. Instead attach a `context` block showing **the code at that anchor as it
stands on the branch today**, and let me decide. Say "Aucune réponse." when there is none.

Omit the key entirely when I have no thread on the PR — most PRs.

**noise** — i18n, generated files, fixtures, renames, formatting. Path and counts only, no diff.
The page collapses them: present so I know they exist, out of the way so they cost me nothing.

### The `facts` block — showing what is not there

"Aucun spec ne couvre cet effet", "aucun chemin d'écriture n'existe pour cet attribut": these are
among the most load-bearing facts a brief carries, and they have no code to embed. Put them in a
`facts` block — a list where the **visible line is the fact itself**, and the chevron appears only
when the item carries a `detail` or a `command`.

**The `output` field is never typed.** Build the item with `B.ran(text, command)`: the command
runs when the block is built and its real output lands in the page, `(aucun résultat)` when it
prints nothing. If the output is not what you expected, change the fact, not the output — a
command that matches elsewhere than you assumed is the brief's first finding, not a nuisance.
`Blocks.fact(text, command, output)` exists only for a result no shell command reproduces (a
scenario run in a console); then the output is pasted from that console, verbatim.

An absence asserted in prose is something I have to take on trust; an absence shown with the
command that establishes it is something I can re-run. Scope the command to what the fact claims:
"no spec exercises X on this path" is established on the spec files of that path, not on `spec/`.

### Excerpt sizing

- **`context` blocks**: the smallest excerpt that stands on its own — usually the **enclosing
  method, whole, signature included**. Not three orphan lines, not the whole file.
- **`hunks`**: keep the body verbatim from `gh pr diff` — leading `+`/`-`/space preserved, nothing
  reflowed. Include only the hunks a step actually talks about; a step is not a dump of the file's
  diff.
- **Trimming a hunk is allowed, and then the `@@` header must be recomputed** so the line numbers
  stay true. A new file arrives as one enormous `@@ -0,0 +1,300 @@`: cut it to the part that carries
  the meaning, write the header for what you kept (`@@ -0,0 +31,41 @@`), and say in the prose what
  the rest contains. "Verbatim" applies to the code lines, not to the header — a header left
  untouched over a trimmed body is a lie about the line numbers.

## Step 4 — Render and open

```
python3 <skill-dir>/render.py \
  ~/.claude/pr-briefs/<repo-short>-<number>.json \
  ~/.claude/pr-briefs/<repo-short>-<number>.html --open
```

`<skill-dir>` is the directory this `SKILL.md` was loaded from; `render.py` sits next to it and
needs nothing beyond the Python standard library. `--open` shows the page in the default browser
on Linux and macOS alike — never call `xdg-open` or `open` yourself. The renderer handles HTML
escaping, diff colouring, both sets of line numbers, and the GitHub links — never hand-write the
HTML.

Then report in the chat: one line per PR, `repo#number — titre — chemin du fichier`. Nothing more;
the brief lives in the page, not in the chat.

**The page embeds the repository's code verbatim.** It is a local file for the reviewer's eyes:
never publish it, upload it, or share it outside the people already allowed to read the repo.

---

## Size discipline

**Measure the PR by its new logic, not by its line count.** Before deciding anything about size,
identify what is *moved* rather than written: a file deleted in one place and added in another, a
rename, a re-namespacing, a reindentation. Confirm it mechanically — normalise both versions
(strip leading whitespace, drop comments and blank lines) and diff them — then say so in the brief
and give the moved part one short step, not five.

**A relocation does not always line up file-to-file.** A method can leave one class and reappear in
a new parent; a controller can lose a `before_action` that an abstract base now carries; a
serializer can be extracted out of another. Pairing whole files misses all of these. When a chunk
disappears from one file, grep its distinctive line across the branch before calling it a deletion —
and when a new file looks familiar, diff it against the sibling it resembles, not only against its
own history. Say plainly "ce fichier est le frère X, à tel détail près": it is often the single most
useful sentence in the brief. A 1200-line PR whose 400 added lines are a
relocation is a 300-line PR to read, and nothing on GitHub tells you that.

The page can be long — it carries the code. What stays tight is the **path**: three to six steps.
If a PR is too large to walk in six steps, say so explicitly ("PR trop large pour un parcours : je
la découpe en N zones") and give one walkthrough per zone. Never silently truncate — if a part of
the change isn't covered, the page must say which part and why.
