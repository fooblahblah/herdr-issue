# herdr-issue

A Claude Code plugin that launches GitHub issues autonomously in a Herdr
worktree and Claude Code session. Several issues named together are fixed
together, in one worktree, session and PR.

```
/herdr-issue:issue 123
/herdr-issue:issue 123 124, 125
/herdr-issue:issue #123, #124, #125
/herdr-issue:issue 123 --plain
/herdr-issue:issue 123 --ultracode
```

The issues get one worktree, branched from the remote default branch, with an
`agent` tab running Claude Code and a spare `shell` tab. One issue gets the
branch `issue/<N>-<slug>`; several get `issue/<N>_<N>...-<slug>`, the slug
taken from the first issue's title. The agent name keeps as many leading
numbers as fit Herdr's 32-character limit. The session is given the prompt in
`plugins/herdr-issue/prompts/autonomous-issue.md` and every issue is assigned
to you. If any issue already has a worktree, alone or with others, or is not
open, nothing is launched and every problem is reported.

There are three modes, and the launch summary names the one used:

- `superpowers`, the default when a `superpowers*` plugin is enabled in your
  user, project or local settings: the session follows the Superpowers
  workflow.
- `plain`, the default otherwise, or with `--plain`: the session follows the
  workflow written in the prompt and needs no other plugin.
- `ultracode`, with `--ultracode`: the session runs in ultracode.

`--plain` and `--ultracode` switch every `superpowers*` plugin off for the
session.

## Requirements

`herdr` (server running), `gh` (authenticated), `git`, `jq`, and a GitHub
`origin` remote.

## Install

```
/plugin marketplace add fooblahblah/herdr-issue
/plugin install herdr-issue@herdr-issue
```

## Per-repository prompt

A repository that carries `.claude/prompts/autonomous-issue.md` uses that
template instead of the plugin's. A line `{{#name}}` keeps the lines up to the
matching `{{/name}}` when `name` is active, and `{{^name}}` keeps them when it
is not; sections nest and each marker sits on its own line. The active names
are the mode (`superpowers`, `plain` or `ultracode`) and, when several issues
are launched, `multi`.

`{{ISSUES}}` becomes the issue URLs as one phrase (`A`, `A and B`,
`A, B and C`) and `{{DEFAULT_BRANCH}}` the remote default branch.
`{{ISSUE_URL}}` names a single issue: a template refuses to launch several
issues when `{{ISSUE_URL}}` would reach the prompt; keep it inside a
`{{^multi}}` section.

## Tests

```
python -m pytest tests
```

The tests drive the real launcher with stub `git`, `gh` and `herdr` on `PATH`;
they need `jq` and `pytest`.

## License

MIT. See [LICENSE](LICENSE).
