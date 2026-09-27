#!/usr/bin/env bash
# Secret leak guard: blocks commits containing real API keys or credentials.
# Install as a pre-commit hook (one-time, per clone):
#   cp scripts/check_secrets.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

# Patterns that indicate a real secret (not a ${ENV_VAR} placeholder)
PATTERNS=(
    'apikey_[A-Za-z0-9]{20,}'
    'sk-[A-Za-z0-9]{20,}'
    'tsk_[A-Za-z0-9]{20,}'
    'ghp_[A-Za-z0-9]{30,}'
    'gho_[A-Za-z0-9]{30,}'
    'AKIA[0-9A-Z]{16}'
    'BEGIN [A-Z ]*PRIVATE KEY'
)

# Staged content only (what would actually be committed).
# Note: '^+++' (three literal pluses) excludes the '+++ b/file' diff header.
# A bracketed '^[+++]' would be a char class matching ANY '+', deleting all content!
STAGED=$(git diff --cached --unified=0 2>/dev/null | grep '^[+]' | grep -v '^+++' || true)

if [ -z "$STAGED" ]; then
    exit 0
fi

for pat in "${PATTERNS[@]}"; do
    if echo "$STAGED" | grep -qE "$pat"; then
        echo "ERROR: possible secret detected (pattern: $pat)" >&2
        echo "Blocked commit. If this is a false positive, use --no-verify." >&2
        exit 1
    fi
done

# Also check: does the local TypeSafe key appear verbatim in staged content?
KEYFILE="$HOME/.config/typesafe/apikey"
if [ -f "$KEYFILE" ]; then
    KEY=$(sed -n 's/^export TYPESAFE_API_KEY=//p' "$KEYFILE" | tr -d '"' | head -1)
    if [ -n "$KEY" ] && echo "$STAGED" | grep -qF "$KEY"; then
        echo "ERROR: TypeSafe API key detected in staged changes!" >&2
        exit 1
    fi
fi

exit 0
