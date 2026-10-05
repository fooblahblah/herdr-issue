---
name: issue
description: Launch one or more GitHub issues autonomously, together in one isolated Herdr worktree, session and PR (uses Superpowers when it is enabled; --plain runs without it, --ultracode runs an ultracode session)
argument-hint: <issue-number>... [--plain | --ultracode]
disable-model-invocation: true
allowed-tools: Bash
---

Launch GitHub issues $ARGUMENTS together in one Herdr worktree, Claude Code session and PR.

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/herdr-issue" "$ARGUMENTS"
```
