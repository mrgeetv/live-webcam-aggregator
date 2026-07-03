#!/bin/bash
# PreToolUse hook: blocks `git commit` when the current branch is main or master.
#
# Registered in .claude/settings.json against `matcher: "Bash"` (every Bash call).
# The security decision is made HERE, not via a settings `if` predicate: that
# predicate's command parser fails open on complex compound commands (for-loops,
# command substitution), firing the hook on unrelated commands. Instead we read
# the literal command from stdin and only act on a genuine `git commit`.
#
# Emits a deny decision as JSON on main/master; silent pass-through otherwise.

set -euo pipefail

# PreToolUse passes the tool call as JSON on stdin; the bash command is at
# .tool_input.command. Fall back to empty if anything is missing.
input=$(cat 2>/dev/null || echo "")
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")

# Only care about actual `git commit` invocations. Match `git` followed by a
# whitespace-prefixed `commit` within the same command segment ([^;&|]* cannot
# cross a ; & | separator, so `git` and `commit` must be in the same segment;
# whatever global flags/options sit between them are tolerated). Requiring
# whitespace before `commit` means `pre-commit` and `git log --grep=commit`
# do NOT match (their `commit` is preceded by `-` or `=`), and a piped
# `... | grep commit` is in a different segment. We err toward over-matching a
# real commit rather than missing one — this is a safety gate.
if ! printf '%s' "$cmd" | grep -Eq '\bgit\b[^;&|]*[[:space:]]commit\b'; then
  exit 0
fi

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

case "$branch" in
  main|master)
    jq -n --arg b "$branch" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: (
          "Blocked: direct commit to protected branch \"" + $b + "\". " +
          "Per project CLAUDE.md, create a feature branch first: " +
          "git checkout -b fix/your-change"
        )
      }
    }'
    ;;
esac
