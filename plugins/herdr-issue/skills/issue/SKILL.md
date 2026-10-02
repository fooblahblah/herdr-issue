---
name: issue
description: Launch one or more GitHub issues autonomously, each in its own isolated Herdr worktree (uses Superpowers when it is enabled; --plain runs without it, --ultracode runs ultracode sessions)
argument-hint: <issue-number>... [--plain | --ultracode]
disable-model-invocation: true
allowed-tools: Bash
---

Launch GitHub issues $ARGUMENTS, each in its own Herdr worktree and Claude Code session.

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/herdr-issue" "$ARGUMENTS"
```
