# herdr-issue

A Claude Code plugin that launches GitHub issues autonomously, each in its own
Herdr worktree and Claude Code session.

```
/herdr-issue:issue 123
/herdr-issue:issue 123 124, 125
/herdr-issue:issue 123 --plain
/herdr-issue:issue 123 --ultracode
```

Each issue gets a worktree on `issue/<N>-<slug>`, branched from the remote
default branch, with an `agent` tab running Claude Code and a spare `shell`
tab. The session is given the prompt in
`plugins/herdr-issue/prompts/autonomous-issue.md` and the issue is assigned to
you. An issue that already has a worktree, or is not open, is skipped.

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
template instead of the plugin's. Lines `{{#superpowers}}` ... `{{/superpowers}}`,
`{{#plain}}` ... `{{/plain}}` and `{{#ultracode}}` ... `{{/ultracode}}` mark
mode-specific text; `{{ISSUE_URL}}` and `{{DEFAULT_BRANCH}}` are substituted.

## Tests

```
python -m pytest tests
```

The tests drive the real launcher with stub `git`, `gh` and `herdr` on `PATH`;
they need `jq` and `pytest`.

## License

MIT. See [LICENSE](LICENSE).
