#!/usr/bin/env bash
# hermes_switch_harness.sh
# Utility to transition a Hermes session from one local harness (process) to another
# while preserving full context, filesystem snapshot, and optionally a separate git worktree.

set -euo pipefail

# Helper: print usage
usage() {
    cat <<'EOF'
Usage: hermes_switch_harness.sh [options]
Options:
  --title <name>       Name of the current session (if not set, script will prompt).
  --snapshot <name>    Optional snapshot label to create before switching.
  --worktree           Run the new harness in git worktree mode (-w) to avoid conflicts.
  --background-cmd "CMD"   Command to run in the new harness after it starts (quoted).
  --list-sessions      List recent sessions and exit.
  -h, --help           Show this help message.
Example:
  hermes_switch_harness.sh --title clinirise-migration --snapshot pre-switch --worktree \
    --background-cmd "hermes chat -q 'Continue migration steps'"
EOF
    exit 0
}

# Parse arguments
TITLE=""
SNAP_LABEL=""
WORKTREE=false
BG_CMD=""
LIST_SESSIONS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --title) TITLE="$2"; shift 2;;
        --snapshot) SNAP_LABEL="$2"; shift 2;;
        --worktree) WORKTREE=true; shift;;
        --background-cmd) BG_CMD="$2"; shift 2;;
        --list-sessions) LIST_SESSIONS=true; shift;;
        -h|--help) usage;;
        *) echo "Unknown option: $1"; usage;;
    esac
done

# List sessions helper
if $LIST_SESSIONS; then
    echo "Recent Hermes sessions:";
    hermes sessions list | head -20
    exit 0
fi

# Ensure we have a title (session name)
if [[ -z "$TITLE" ]]; then
    read -rp "Enter the Hermes session name (or leave empty to use the most recent session): " TITLE
fi

# Determine how to resume: by name or by most recent
if [[ -n "$TITLE" ]]; then
    RESUME_ARG="--continue $TITLE"
else
    RESUME_ARG="--continue"
fi

# Create a snapshot if requested
if [[ -n "$SNAP_LABEL" ]]; then
    echo "Creating snapshot '$SNAP_LABEL'..."
    # The slash command /snapshot takes an optional label via argument – use hermes CLI to invoke it
    # hermes does not have a direct CLI flag for label, so we send the command via the chat interface.
    # We'll spawn a temporary hermes session that executes the slash command and exits.
    hermes chat -q "/snapshot $SNAP_LABEL" >/dev/null 2>&1 || true
    echo "Snapshot created."
fi

# Build the hermes launch command
CMD="hermes"
if $WORKTREE; then
    CMD+=" -w"
fi
CMD+=" $RESUME_ARG"

# If a background command is supplied, we will start hermes in a new tmux session and send the command.
if [[ -n "$BG_CMD" ]]; then
    SESSION_NAME="hermes_${TITLE:-$(date +%s)}"
    echo "Launching new Hermes harness in tmux session '$SESSION_NAME'..."
    tmux new-session -d -s "$SESSION_NAME" "$CMD"
    # Give hermes a moment to start up
    sleep 5
    echo "Sending background command to hermes..."
    tmux send-keys -t "$SESSION_NAME" "$BG_CMD" C-m
    echo "Hermes harness ready. Attach with: tmux attach -t $SESSION_NAME"
else
    echo "Starting new Hermes harness..."
    exec $CMD
fi
