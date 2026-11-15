---
name: issue
description: Launch one or more GitHub issues autonomously, together in one isolated worktree, Claude Code session and PR in Herdr or Orca (uses Superpowers when it is enabled; --plain runs without it, --ultracode runs an ultracode session; Orca refuses --ultracode, and --plain while Superpowers is enabled)
argument-hint: <issue-number>... [--plain | --ultracode] [--model <model>] [--effort <level>] [--backend herdr|orca]
disable-model-invocation: true
allowed-tools: Bash
---

Launch GitHub issues $ARGUMENTS together in one worktree, Claude Code session and PR, in Herdr or Orca.

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/issue-herd" "$ARGUMENTS"
```
