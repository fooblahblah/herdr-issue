# issue-herd

## Versioning

Every merge to `main` that changes what users install, meaning anything under
`plugins/issue-herd/` or `.claude-plugin/marketplace.json`, bumps `version`
in `plugins/issue-herd/.claude-plugin/plugin.json`, in the same PR or in a
release PR merged right after it. Users only get an update when the version
changes. Changes that leave the installed plugin as it was, such as tests or
`docs/`, need no bump.

The project is pre-1.0:

- minor (0.10.0 to 0.11.0) for a new feature or a change in behaviour users
  would notice;
- patch (0.10.0 to 0.10.1) for a fix or a change users would not notice.

When it is not clear whether a change is a feature or a fix, ask before
merging rather than picking one. A release commit is titled
`Release <version>: <what it brings>`.
