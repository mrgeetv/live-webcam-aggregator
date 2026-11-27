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
# in a worktree is not the project's. In a plain `cd <dir> && ... git commit`
# that is the last cd's directory (else the hook input's .cwd), moved by the
# commit's own `git -C <dir>`. Anything this can't read for certain (a subshell
# or `$(...)`, `||`, a conditional or loop, pushd/popd, `cd -`, GIT_DIR and
# friends, two -C, a target that isn't a directory) fails CLOSED: the commit is
# denied if ANY directory involved (cwd, project, every cd and -C target) is on
# a protected branch. A guess that picked the wrong one would fail open.
protected() { [[ $1 == main || $1 == master || $1 == next ]]; }
branch_of() { git -C "$1" rev-parse --abbrev-ref HEAD 2>/dev/null || true; }
unquote() { local p=${1#[\"\']}; p=${p%[\"\']}; [[ $p == "~"* ]] && p=$HOME${p#\~}; printf '%s' "$p"; }
resolve() { if [[ $2 == /* ]]; then printf '%s' "$2"; else printf '%s/%s' "$1" "$2"; fi; }

project=${CLAUDE_PROJECT_DIR:-.}
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[[ -n $cwd ]] || cwd=$project
before=$(printf '%s' "$cmd" | sed -E 's/\bgit\b[^;&|]*[[:space:]]commit\b.*//')
commit=$(printf '%s' "$cmd" | grep -oE '\bgit\b[^;&|]*[[:space:]]commit\b' | head -n1 || true)
mapfile -t cds < <(printf '%s' "$before" | grep -oE '(^|[;&|(][[:space:]]*)cd[[:space:]]+[^[:space:];&|)]+' | sed -E 's/.*cd[[:space:]]+//' || true)
mapfile -t cs < <(printf '%s' "$commit" | grep -oE '[[:space:]]-C[[:space:]]+[^[:space:]]+' | sed -E 's/.*-C[[:space:]]+//' || true)

certain=true
if printf '%s' "$before$commit" | grep -Eq '[()`]|\|\||\b(if|then|else|for|while|until|case|pushd|popd)\b|GIT_DIR|GIT_WORK_TREE|--git-dir|--work-tree|\bcd[[:space:]]+-' \
  || ((${#cs[@]} > 1)); then
  certain=false
fi

dir=$cwd
seen=("$cwd" "$project")
for t in "${cds[@]}"; do
  dir=$(resolve "$dir" "$(unquote "$t")")
  seen+=("$dir")
done
for t in "${cs[@]}"; do
  dir=$(resolve "$dir" "$(unquote "$t")")
  seen+=("$dir")
done
[[ -d $dir ]] || certain=false

if $certain; then
  branch=$(branch_of "$dir")
else
  branch=""
  for d in "${seen[@]}"; do
    b=$(branch_of "$d")
    if protected "$b"; then branch=$b; break; fi
  done
fi
# No branch (not a repo, or nothing protected found while unsure): the project's
# branch decides, as an empty branch would fail open.
[[ -n $branch ]] || branch=$(branch_of "$project")

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
