---
name: send
description: Send a message to the running Claude Code session of a GitHub issue launched by issue-herd, in Herdr or Orca, as if typed at its prompt. Use to steer a session, answer it, or pass it new information; the issue number names the session.
argument-hint: <issue-number> <text>...
allowed-tools: Bash
---

Send the message to the session working on issue $ARGUMENTS (the first word is the issue number, the rest is the message), in Herdr or Orca.

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/issue-send" "$ARGUMENTS"
```
