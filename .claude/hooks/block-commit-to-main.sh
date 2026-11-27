#!/bin/bash
# PreToolUse hook: blocks `git commit` when the current branch is main, master or
# next (the shared branch for the coming major).
#
# Registered in .claude/settings.json against `matcher: "Bash"` (every Bash call).
# The security decision is made HERE, not via a settings `if` predicate: that
# predicate's command parser fails open on complex compound commands (for-loops,
# command substitution), firing the hook on unrelated commands. Instead we read
# the literal command from stdin and only act on a genuine `git commit`.
#
# Emits a deny decision as JSON on those branches; silent pass-through otherwise.

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

# The branch that matters is the one checked out where the commit runs, which
# in a worktree is not the project's. That directory is the shell's cwd (the
# hook input's .cwd), moved by the last `cd <dir>` before the commit and then
# by the commit's own `git -C <dir>`. A target we can't resolve (a variable, a missing dir)
# falls back to the project dir: an empty branch would silently fail OPEN.
unquote() { local p=${1#[\"\']}; p=${p%[\"\']}; [[ $p == "~"* ]] && p=$HOME${p#\~}; printf '%s' "$p"; }
resolve() { if [[ $2 == /* ]]; then printf '%s' "$2"; else printf '%s/%s' "$1" "$2"; fi; }

project=${CLAUDE_PROJECT_DIR:-.}
dir=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[[ -n $dir ]] || dir=$project
before=$(printf '%s' "$cmd" | sed -E 's/\bgit\b[^;&|]*[[:space:]]commit\b.*//')
cd_target=$(printf '%s' "$before" | grep -oE '(^|[;&|(][[:space:]]*)cd[[:space:]]+[^[:space:];&|)]+' | tail -n1 | sed -E 's/.*cd[[:space:]]+//' || true)
[[ -n $cd_target ]] && dir=$(resolve "$dir" "$(unquote "$cd_target")")
commit=$(printf '%s' "$cmd" | grep -oE '\bgit\b[^;&|]*[[:space:]]commit\b' | head -n1 || true)
c_target=$(printf '%s' "$commit" | grep -oE '[[:space:]]-C[[:space:]]+[^[:space:]]+' | head -n1 | sed -E 's/.*-C[[:space:]]+//' || true)
[[ -n $c_target ]] && dir=$(resolve "$dir" "$(unquote "$c_target")")

branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
[[ -n $branch ]] || branch=$(git -C "$project" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

case "$branch" in
  main|master|next)
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
