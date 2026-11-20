# issue-herd

A Claude Code plugin that launches GitHub issues autonomously in a worktree
and Claude Code session of [Herdr](https://herdr.dev) or
[Orca](https://orca.dev). Several issues named together are fixed together, in
one worktree, session and PR.

```
/issue-herd:issue 123
/issue-herd:issue 123 124, 125
/issue-herd:issue #123, #124, #125
/issue-herd:issue 123 --plain
/issue-herd:issue 123 --ultracode
/issue-herd:issue 123 --backend orca
/issue-herd:issue 123 --agent antigravity
```

The issues get one worktree, branched from the remote default branch, with a
tab running Claude Code (named `agent` in Herdr). One issue gets the
branch `issue/<N>-<slug>`; several get `issue/<N>_<N>...-<slug>`, the slug
taken from the first issue's title. The session is given the prompt in
`plugins/issue-herd/prompts/autonomous-issue.md` and every issue is assigned
to you. If any issue already has a worktree, alone or with others, or is not
open, nothing is launched and every problem is reported.

## Hosts

The worktree and session live in Herdr or in Orca. A launch from a Herdr pane
goes to Herdr and one from an Orca terminal goes to Orca (they export
`HERDR_PANE_ID` and `ORCA_TERMINAL_HANDLE`). From any other terminal, whichever
host is running is used; when both are, `--backend herdr` or `--backend orca`
decides, and the flag overrides the detection anywhere. The launch summary
names the host.

In Herdr the agent is named `issue-<N>[_<N>...]`, keeping as many leading
numbers as fit Herdr's 32-character limit, and the plugin labels the sidebar
as described below.

In Orca the repository must already be added to Orca. The worktree is created
with `orca worktree create`, named `#<N> <title>`, with the issues linked to
it, so the workspace card shows them and, once the session opens one, the PR.
Claude runs in a terminal tab whatever the Chat | CLI setting, with the
permission mode Orca's agent settings give it. Orca starts Claude itself and
accepts only a model and an effort for it, so the two modes that need a
per-session settings file are refused there: `--ultracode` always, and
`--plain` while a Superpowers plugin is enabled. Since the new worktree has no
`.claude/settings.local.json`, only the user and project settings count
towards Superpowers there. Orca offers no effort for Haiku, so a Haiku session
starts without one. If an issue's branch name is taken, Orca adds a numeric
suffix and the launch summary names the branch Orca made. Everything else,
the prompt, the models config, `pr-land` and the final review, is the same.

Either host's launch refuses an issue that has a worktree made by the other
host, or by hand, since git lists every worktree. Orca's command-line tool is
looked for on `PATH` and then in Orca's shim at
`~/.config/orca/linux-orca-cli-shim/orca`, which is not on `PATH` in a Herdr
pane.

## Antigravity

`--agent antigravity` runs the session in the Antigravity CLI (`agy`) in
place of Claude Code, in either host: Orca starts it with
`--agent antigravity`, and Herdr with `herdr agent start --kind agy`. It runs
`gemini-4-neon-high` unless `--model` or the config names another model ID
(`agy models` lists them). Antigravity puts the effort in the model ID
(`gemini-4-neon-low` up to `gemini-4-neon-xhigh`), so `--effort` is refused
with it, and so is `--ultracode`. Superpowers is a Claude Code plugin, so an
Antigravity session always runs in `plain` mode, whatever plugins are enabled.

The prompt is the same apart from what only Claude Code has: there is no
model routing for subagents, the final whole-branch review is a pass of the
session's own rather than the `final-reviewer` agent, and `pr-land` runs in
the foreground rather than under a Monitor. In Herdr the sidebar names the
issues but not the PR, since the hook that adds it runs only in Claude Code.

To make Antigravity the default, set the agent in a config file:

```json
{ "agent": "antigravity", "antigravity": { "model": "gemini-4-neon-high" } }
```

`--agent claude` then picks Claude Code for one launch.

Antigravity reads `AGENTS.md` and `GEMINI.md`, not `CLAUDE.md`, and does not
load a repository's `.claude/` rules, agents or hooks, so a repository that
keeps its instructions there should carry what matters in `AGENTS.md` too.

## Messaging a session

A running session can be given a message as if it were typed at its prompt,
to steer it, answer it or pass it new information:

```
/issue-herd:send 123 The fix also needs a changelog entry.
plugins/issue-herd/scripts/issue-send 123 < notes.md
```

The first word is the issue number (`#123` works too) and the rest is the
message; with no text, the script reads the message from stdin. Any issue of
a shared session reaches that session. The session is found by its issue,
through the branch of its worktree, in whichever host is running it: in Herdr
the message goes to the agent `issue-<N>...` through `herdr agent prompt`,
and in Orca to the Claude or Antigravity terminal of the issue's workspace
through `orca terminal send`. Either host queues a message behind the
session's current turn. The reply says where it went and, in Orca, whether the session
took it up at once or will when its turn ends. Herdr refuses a message while
the agent waits on a permission prompt. `--backend herdr|orca` before the
issue number looks in one host only. Nothing is sent when the issue has no
worktree or no host has a session for it. The launch summary ends with the
command for the session it launched, so a coordinating session can message
the ones it starts.

The session takes the PR through to merge on its own, with one exception
for scope. It fixes what belongs with the change and opens an issue unasked
only for plainly separate work. When something it finds needs your choice, or
could go either way between the PR and a new issue, it asks you, and it does
not mark the PR ready or land it until you answer.

A customer-facing change (UI, public API, CLI output, or anything else a
customer could check by hand) is first verified the way a customer would use it: in a headless
browser through Playwright for UI, before and after the fix. When that is
conclusive, the session merges. When it is not (the problem would not
reproduce, the app would not run, or a criterion needs a person's judgment),
the session marks the PR ready, says what it could and could not verify,
gives steps for you to check the rest, and merges only once you approve.

In Herdr, the line under the workspace in the sidebar shows `Issue: #123` in
place of the agent name (`Issues: #123, #124` for several), and once the
session opens its PR, `Issue: #123 · PR #456`. The plugin's `PostToolUse` hook
adds the PR after any `gh pr` command on an `issue/` branch in a Herdr pane, so
it also puts the label back after a Herdr restart; it leaves other sessions
alone, Orca's included, where the workspace card shows the PR by itself. Only
the displayed name changes; the agent keeps its name. To set it by hand from
the session's pane, run `plugins/issue-herd/scripts/show-pr [<PR>]`.

The session reproduces the problem before fixing it, but a reproduction is
not automatically a permanent test. Before the PR is ready it keeps a test
only when it guards a lasting property of the product and is cheap; an
expensive guard keeps a small case in the main suite and puts the full sweep
in the repository's less frequent tier; anything else stays out of the
commit, recorded in the PR's Verification section. Where the repository
documents its own test tiers, the session follows them.

The session lands the PR with `plugins/issue-herd/scripts/pr-land`, so
sessions running side by side do not stall each other. Once only CI stands
between the PR and its merge, the session runs it under a Monitor:

```
pr-land <PR> -- --squash
```

Run from the PR's branch, it first waits for GitHub to show the local HEAD.
It then checks the PR about once a minute. When the default branch has moved
and the repository requires branches to be up to date (through branch
protection, which only an admin can read, or a ruleset), it
updates the branch at once, without waiting for the CI run in progress, and
cancels the PR branch's runs on the old head. The update is always a merge
commit, never a rebase. Once every check on the current head passes, required
or not, counting only the latest attempt of a re-run check, it merges with the
flags after `--`, which must name a merge method. It stops, with the reason on
its last line, on checks that fail on two polls in a row, a merge conflict, a
draft, checks pending for two hours, or a block other than CI, and the
session takes over.

When the base branch requires a merge queue, pr-land never updates the branch.
Once every check passes, it adds the PR to the queue through GraphQL
(`enqueuePullRequest`, pinned to the head whose checks passed), because
`gh pr merge` cannot add a PR to the queue in a repository without auto-merge.
It then follows the queue until the PR merges. If the queue removes the PR
without merging it, pr-land stops and gives the queue's reason. The queue
merges with its own method and commit message, so the flags after `--` do not
apply there. `pr-land --stop` stops the lander but leaves the PR in the queue.

The lander runs apart from the command that started it and logs under
`.git/issue-herd/`. Running the command again for the same PR follows the
lander already running, so a Monitor can expire and be re-armed.
`pr-land --stop <PR>` stops it, and it stops itself after an hour with
nothing following it.

There are three modes, and the launch summary names the one used:

- `superpowers`, the default when a `superpowers*` plugin is enabled in your
  user, project or local settings: the session follows the Superpowers
  workflow.
- `plain`, the default otherwise, or with `--plain`: the session follows the
  workflow written in the prompt and needs no other plugin.
- `ultracode`, with `--ultracode`: the session runs in ultracode.

`--plain` and `--ultracode` switch every `superpowers*` plugin off for the
session.

## Models and effort

Out of the box the session runs Opus 5.5 at high effort (xhigh in ultracode).
It routes subagents to Haiku 5.5, Sonnet 5.5 or Opus 5.5 by task, and runs
the final whole-branch review on Opus 5.5 at xhigh. Every one of these choices
can be changed in a JSON config, merged in this order with later sources
winning key by key:

1. `plugins/issue-herd/config/defaults.json`, the shipped defaults;
2. `~/.config/issue-herd/config.json` (or `$XDG_CONFIG_HOME/issue-herd/config.json`);
3. `.claude/issue-herd.json` in the repository;
4. `--model` and `--effort` on the command line, for the session only.

```json
{
  "coordinator": { "model": "fable", "effort": "high" },
  "ultracode_coordinator": { "effort": "xhigh" },
  "tiers": {
    "light": { "model": "haiku" },
    "standard": { "model": "opus", "effort": "medium" },
    "heavy": { "model": "opus" }
  },
  "final_review": { "model": "fable", "effort": "high" }
}
```

A file only needs the keys it changes. `{"tiers": {"standard": {"model": "opus", "effort": "medium"}}}`
alone routes the everyday work to Opus 5.5 at medium effort.

- `coordinator` is the session itself. Its model goes to `claude --model`, so
  it may be an alias or a full model ID such as `claude-fable-5-2`.
- `ultracode_coordinator` is the session under `--ultracode`. It takes the
  coordinator's model unless it names its own.
- `tiers` are the subagent routes the prompt describes: `light` for
  transcription and single-file mechanical fixes, `standard` for most
  implementation, `heavy` for design, hard debugging and judgment-heavy
  review. A tier without an `effort` (or with `null`) inherits the session's.
- `final_review` is the model and effort the session dispatches the
  `final-reviewer` agent with.
- `agent` is `claude` (the default) or `antigravity`, and `antigravity.model`
  the Antigravity model ID; see [Antigravity](#antigravity). The other keys
  apply only to Claude Code.

Tier and final-review models must be `haiku`, `sonnet`, `opus` or `fable`,
the names Claude Code's Agent tool accepts. Effort is one of `low`, `medium`,
`high`, `xhigh` or `max`. A config with an unknown key or a bad value
launches nothing and names the file and key. The launch summary shows the
session's model and effort.

```
/issue-herd:issue 123 --model fable
/issue-herd:issue 123 --effort xhigh
```

## Requirements

`herdr` (server running) or `orca` (app running, with the repository added to
it), `gh` (authenticated), `git`, `jq`, and a GitHub `origin` remote. For
`--agent antigravity`, the `agy` CLI, signed in.

## Install

```
/plugin marketplace add fooblahblah/issue-herd
/plugin install issue-herd@issue-herd
```

## Per-repository prompt

A repository that carries `.claude/prompts/autonomous-issue.md` uses that
template instead of the plugin's. A line `{{#name}}` keeps the lines up to the
matching `{{/name}}` when `name` is active, and `{{^name}}` keeps them when it
is not; sections nest and each marker sits on its own line. The active names
are the mode (`superpowers`, `plain` or `ultracode`), the agent (`claude` or
`antigravity`) and, when several issues are launched, `multi`.

`{{ISSUES}}` becomes the issue URLs as one phrase (`A`, `A and B`,
`A, B and C`), `{{DEFAULT_BRANCH}}` the remote default branch, and
`{{PR_LAND}}` the path of the plugin's `pr-land` script.

The configured models fill these:

- `{{COORDINATOR_MODEL}}` and `{{EFFORT}}`: the session's model name
  (`Opus 5.5`) and effort (`high`);
- `{{LIGHT_MODEL}}`, `{{STANDARD_MODEL}}` and `{{HEAVY_MODEL}}`: each routing
  tier's model name, followed by ` at <effort> effort` when the tier sets one;
- `{{FINAL_REVIEW_MODEL}}`: the final review's model name;
  `{{FINAL_REVIEW_ALIAS}}` and `{{FINAL_REVIEW_EFFORT}}`: the model alias and
  effort to dispatch it with.
`{{ISSUE_URL}}` names a single issue: a template refuses to launch several
issues when `{{ISSUE_URL}}` would reach the prompt; keep it inside a
`{{^multi}}` section.

## Tests

```
python -m pytest tests
```

The tests drive the real launcher, `issue-send` and `pr-land` with stub `git`, `gh`, `herdr`
and `orca` on `PATH`; they need `git`, `jq` and `pytest`.

## License

MIT. See [LICENSE](LICENSE).
