# pr-review-toolkit

Three [Claude Code](https://claude.com/claude-code) skills that cover a pull request review from
end to end, in the order a reviewer actually works:

| Skill | Question it answers | Writes to GitHub? |
|---|---|---|
| [`pr-brief`](skills/pr-brief/SKILL.md) | *What does this PR do, and what does it touch that the diff doesn't show?* | No |
| [`pr-flow`](skills/pr-flow/SKILL.md) | *Who triggers what, through which components, and what fans out where?* | No |
| [`review-requested-prs`](skills/review-requested-prs/SKILL.md) | *What should I say about it?* | Pending review only, never submitted |

The first two build your understanding without judging the code. The third drafts the review, as
**pending comments you finish and submit yourself**. Nothing is ever submitted on your behalf.

## pr-brief — understand before you review

Builds one self-contained HTML page per PR: the change explained as a **reading path** (entry point
first, three to six steps), with every relevant diff hunk and every out-of-diff excerpt embedded
inline. Callers, callbacks, migrations, specs, feature flags: the code the diff reaches but does not
show.

Two rules shape it:

- **Zero round-trip.** If reading the page requires opening your editor or GitHub, the page failed.
  Every piece of code the brief refers to is embedded. No "see `x.rb:12`".
- **Describe, never evaluate.** The brief states facts ("no spec covers `Cart#empty?`") and stops.
  Drawing conclusions is your job. If the brief criticised the code, you would read its criticism
  instead of building your own model.

It also collects **your own** earlier threads on the PR, including pending drafts, next to the code
as it stands on the branch today. Other reviewers' feedback is deliberately left out.

```
/pr-review-toolkit:pr-brief                    # pick among the PRs awaiting your review
/pr-review-toolkit:pr-brief 4261               # one PR in the current repo
/pr-review-toolkit:pr-brief owner/repo#4261    # one PR anywhere
```

Output: `~/.claude/pr-briefs/<repo>-<number>.html`, opened in your browser. The JSON next to it
allows a re-render without redoing the analysis.

## pr-flow — see the mechanism

For PRs spread across callbacks, jobs, services and entry points, where a linear reading path is not
enough. Draws the mechanism as a left-to-right SVG diagram: triggers, the components they reach,
what travels on each arrow, and the paths that *don't* reach the new code. **Every box is
clickable** and opens the diff hunks and out-of-diff excerpts it stands for, with the decisive lines
highlighted.

Same rules as `pr-brief`: read-only, facts only, code embedded. Reuses the brief's JSON when one
exists.

```
/pr-review-toolkit:pr-flow owner/repo#4261
```

Output: `~/.claude/pr-flows/<repo>-<number>.html`.

## review-requested-prs — draft the review

Walks through the open PRs **awaiting your requested review**, across one or several repos, and
drafts a GitHub review for each as a **pending review**: inline comments plus a summary, visible
only to you until you click *Submit review* on GitHub.

1. Discovers the PRs where your review is requested and still pending
   (`gh search prs --review-requested=@me`), excluding drafts.
2. Lets you pick the repos, then the PRs, with everything selected by default.
3. Reviews each PR as if a junior wrote it: correctness, security, simplicity, conventions, tests.
   Reads prior feedback first so it does not repeat what has already been raised.
4. Leaves the feedback pending. You edit, approve or discard each comment on your own judgment.

Comments are actionable only, no filler praise. It won't create a second pending review on a PR
that already has one of yours.

```
/pr-review-toolkit:review-requested-prs            # live: creates pending reviews
/pr-review-toolkit:review-requested-prs dry-run    # prints what it would post, writes nothing
```

## Why three skills

A full review dumped as one block of text is hard to digest, and a review written before the
reviewer understands the change is worth little. Splitting the work keeps each step honest:
`pr-brief` and `pr-flow` refuse to judge, so you build your own model of the code first;
`review-requested-prs` puts the feedback on the PR itself, anchored to lines, and leaves the final
word to you.

## Requirements

- [Claude Code](https://claude.com/claude-code)
- [GitHub CLI](https://cli.github.com/) (`gh`) authenticated with `repo` scope. Check with
  `gh auth status`.
- Python 3 for the two renderers (`render.py`, `blocks.py`). Standard library only, no packages.
  Linux and macOS are both supported: the pages open in the default browser through Python's
  `webbrowser` module, so no `xdg-open`/`open` dependency.

## Install

### As a plugin (recommended)

```
/plugin marketplace add yoones/pr-review-toolkit
/plugin install pr-review-toolkit@yoones
```

Reload when prompted; the three skills then appear in your `/` menu, namespaced as
`/pr-review-toolkit:<skill>`. Updates ship by re-running `/plugin marketplace update yoones`.

If you installed the previous `review-requested-prs` plugin from this repo, uninstall it and
install `pr-review-toolkit` instead. The old marketplace URL keeps working through GitHub's rename
redirect, but the plugin name changed.

### Manually

Copy the skill directories into your Claude Code skills folder:

```
git clone https://github.com/yoones/pr-review-toolkit
cp -r pr-review-toolkit/skills/* ~/.claude/skills/
```

Claude Code loads each `SKILL.md` and exposes them as `/pr-brief`, `/pr-flow` and
`/review-requested-prs`.

## Notes

- **The brief and flow pages embed your repository's code verbatim.** They are local files for the
  reviewer's eyes. Do not publish or upload them.
- The pages are written in **French prose with source-code names kept verbatim**. To switch
  language, edit the *Language* section of `skills/pr-brief/SKILL.md` and the matching bullet in
  `skills/pr-flow/SKILL.md`.
- The review prompts lean toward a Ruby on Rails codebase in the themes they watch, but the
  mechanics work for any repo.
- `pr-brief` and `pr-flow` read PR branches through named refs (`refs/pr-brief/<N>`) without
  checking anything out, clone into a scratch directory when the repo is not available locally, and
  delete the ref when done. `review-requested-prs` checks PRs out in isolated git worktrees and
  removes them afterwards. Your working tree is left as it was.

## License

MIT, see [LICENSE](LICENSE).
