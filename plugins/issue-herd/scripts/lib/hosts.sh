# Shared by issue-herd and issue-send: how the launcher and the messenger find
# the hosts, Herdr and Orca, and an issue's worktree. Sourced, never run; the
# caller has set -euo pipefail and provides $repo.

# The agent name for issue numbers joined by "_": issue-12_15_17, with
# trailing numbers dropped until it fits. Herdr caps names at 32 characters;
# two launches can only end up with the same shortened name when they share
# their first issue, which the worktree check refuses.
agent_name_for() {
    local ids="$1"
    while [[ ${#ids} -gt 26 && "$ids" == *_* ]]; do
        ids="${ids%_*}"
    done
    echo "issue-${ids}"
}

# On Linux, Orca's CLI is a shim in its config directory, which is not always
# on PATH (it is not in a Herdr pane), so look there too.
orca_shim_dir="${XDG_CONFIG_HOME:-$HOME/.config}/orca/linux-orca-cli-shim"
if ! command -v orca >/dev/null && [[ -x "${orca_shim_dir}/orca" ]]; then
    PATH="${orca_shim_dir}:${PATH}"
fi
herdr_up() { command -v herdr >/dev/null && herdr status server >/dev/null 2>&1; }
orca_up() {
    command -v orca >/dev/null &&
        orca status --json 2>/dev/null | jq -e '.ok and .result.runtime.reachable' >/dev/null 2>&1
}

# The host that gets the worktree and the Claude session: Herdr or Orca. A
# Herdr pane exports HERDR_PANE_ID and an Orca terminal ORCA_TERMINAL_HANDLE,
# so a launch from inside either host goes to that host. From any other
# terminal, the host that is running is used, and --backend settles it when
# both or neither are. Takes $backend, set by --backend or empty; leaves
# $backend and $host_name set, or exits with the reason.
select_backend() {
    if [[ -z "$backend" ]]; then
        if [[ -n "${HERDR_PANE_ID:-}" ]]; then
            backend=herdr
        elif [[ -n "${ORCA_TERMINAL_HANDLE:-}" ]]; then
            backend=orca
        else
            local herdr_running="" orca_running=""
            if herdr_up; then herdr_running=1; fi
            if orca_up; then orca_running=1; fi
            if [[ -n "$herdr_running" && -n "$orca_running" ]]; then
                echo "Both Herdr and Orca are running; pass --backend herdr or --backend orca." >&2
                exit 1
            elif [[ -n "$herdr_running" ]]; then
                backend=herdr
            elif [[ -n "$orca_running" ]]; then
                backend=orca
            else
                echo "Neither Herdr nor Orca is running. Start one first." >&2
                exit 1
            fi
        fi
    fi

    case "$backend" in
        herdr)
            if ! herdr_up; then
                echo "Herdr server is not running. Start Herdr first." >&2
                exit 1
            fi
            host_name=Herdr
            ;;
        orca)
            if ! command -v orca >/dev/null; then
                echo "The orca command was not found on PATH or in ${orca_shim_dir}." \
                    "Put Orca's command-line tool on PATH." >&2
                exit 1
            fi
            if ! orca_up; then
                echo "Orca is not running. Start Orca first." >&2
                exit 1
            fi
            host_name=Orca
            ;;
    esac
}

# Git's own list of the repository's worktrees as a JSON list of
# {branch, path, workspace, own}, the shape the hosts' lists take in the
# launcher. Its first entry is the main checkout, which is how Orca knows the
# repository even when the command runs inside a linked worktree. Leaves
# $git_worktrees and $main_checkout set.
list_git_worktrees() {
    git_worktrees="$(
        git -C "$repo" worktree list --porcelain |
            jq -R -s -c '[split("\n\n")[] | split("\n")
                | select(any(.[]; startswith("worktree ")))
                | {branch: ((first(.[] | select(startswith("branch "))) // "")
                            | ltrimstr("branch ") | ltrimstr("refs/heads/")),
                   path: (first(.[] | select(startswith("worktree "))) | ltrimstr("worktree ")),
                   workspace: "", own: false}]'
    )"
    main_checkout="$(jq -r '.[0].path // empty' <<<"$git_worktrees")"
    main_checkout="${main_checkout:-$repo}"
}

# The first worktree in the JSON list $1 that holds issue $2, alone or with
# others, with its issue numbers added as ids ("12_15"); nothing when none
# does. A branch names its issues as issue/<N>[_<N>...]-<slug>; the match is
# on the numbers rather than the full name, so a retitled issue is still
# found.
issue_worktree_in() {
    jq -c --arg n "$2" '
        def issue_numbers:
            (.branch // "")
            | (capture("^issue/(?<n>[0-9]+(_[0-9]+)*)-") | .n | split("_")) // [];
        first(
            .[]
            | select(any(issue_numbers[]; . == $n))
            | . + {ids: (issue_numbers | join("_"))}
        ) // empty
    ' <<<"$1"
}
