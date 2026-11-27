#!/bin/bash
# PreToolUse hook: blocks `git commit` when the branch checked out where the
# commit runs is main, master or next (the shared branch for the coming major),
# and any commit whose location it can't establish.
#
# Registered in .claude/settings.json against `matcher: "Bash"` (every Bash call).
# The security decision is made HERE, not via a settings `if` predicate: that
# predicate's command parser fails open on complex compound commands (for-loops,
# command substitution), firing the hook on unrelated commands. Instead we read
# the literal command from stdin and only act on a genuine `git commit`.
#
# Emits a deny decision as JSON; silent pass-through otherwise.

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

deny() {
  jq -n --arg r "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: ("Blocked: " + $r)
    }
  }'
  exit 0
}
protected() { [[ $1 == main || $1 == master || $1 == next ]]; }
branch_of() { git -C "$1" rev-parse --abbrev-ref HEAD 2>/dev/null || true; }
unquote() { local p=${1#[\"\']}; p=${p%[\"\']}; [[ $p == "~"* ]] && p=$HOME${p#\~}; printf '%s' "$p"; }
resolve() { if [[ $2 == /* ]]; then printf '%s' "$2"; else printf '%s/%s' "$1" "$2"; fi; }
deny_branch() {
  deny "direct commit to protected branch \"$1\". Per project CLAUDE.md, create a feature branch first: git checkout -b fix/your-change"
}

# Something other than the directory picks the repository: the hook can't
# follow it, so it refuses rather than guess.
if printf '%s' "$cmd" | grep -Eq 'GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR|--git-dir|--work-tree|core\.worktree'; then
  deny "this commit names its repository through GIT_DIR, --git-dir, --work-tree or core.worktree, which this hook can't check. Commit from the worktree's own directory instead."
fi

# The branch that matters is the one checked out where the commit runs, which
# in a worktree is not the project's. The hook is CERTAIN of that directory only
# when everything before the commit is an allowlisted shape: `cd <literal dir>`,
# or a command from SAFE, which can't move the shell (so no eval, exec, sh -c,
# pushd, subshells, `$(...)`, `||`, braces or variable assignments), joined by
# && or ;. Then the last cd's directory (else the hook input's .cwd), moved by
# the commit's own `git -C`, decides. Otherwise it is UNSURE and checks every
# directory involved (cwd, project, each cd and -C target): any on a protected
# branch, or any target that doesn't resolve to a directory, denies.
SAFE=" git uv echo printf true ls cat gh python3 jq grep sed head tail wc cut sort mkdir cp mv touch test "
project=${CLAUDE_PROJECT_DIR:-.}
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[[ -n $cwd ]] || cwd=$project
# -z: the whole command is one record, so a heredoc's lines after the commit
# (its message) are cut away with it instead of read as commands before it.
before=$(printf '%s' "$cmd" | sed -zE 's/\bgit\b[^;&|]*[[:space:]]commit\b.*//' | tr -d '\0')
commit=$(printf '%s' "$cmd" | grep -oE '\bgit\b[^;&|]*[[:space:]]commit\b' | head -n1 || true)
mapfile -t cds < <(printf '%s' "$before" | grep -oE '(^|[^[:alnum:]_-])cd[[:space:]]+[^[:space:];&|)]+' | sed -E 's/.*cd[[:space:]]+//' || true)
mapfile -t cs < <(printf '%s' "$commit" | grep -oE '[[:space:]]-C[[:space:]]*[^[:space:]]+' | sed -E 's/.*-C[[:space:]]*//' || true)

certain=true
if [[ $before$commit == *'||'* || $before$commit == *'$('* || $before$commit == *'`'* ]] || ((${#cs[@]} > 1)); then
  certain=false
fi
mapfile -t segs < <(printf '%s' "$before" | sed -E 's/&&|;|\|/\n/g')
for seg in "${segs[@]}"; do
  seg=$(printf '%s' "$seg" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')
  [[ -z $seg ]] && continue
  [[ $seg =~ ^cd[[:space:]]+(\"[^\"\$\`]*\"|\'[^\']*\'|[^[:space:]\"\'\$\`\(\)\{\}\;\&\|]+)$ ]] && continue
  [[ $seg == *[\(\)\{\}\$\`]* || $SAFE != *" ${seg%%[[:space:]]*} "* ]] && certain=false
done

dir=$cwd
seen=("$cwd" "$project")
for t in "${cds[@]}" "${cs[@]}"; do
  dir=$(resolve "$dir" "$(unquote "$t")")
  seen+=("$dir")
done

if $certain && [[ -d $dir ]]; then
  branch=$(branch_of "$dir")
  # Not a repo: git commit fails there anyway, so the project's branch decides.
  [[ -n $branch ]] || branch=$(branch_of "$project")
  if protected "$branch"; then deny_branch "$branch"; fi
  exit 0
fi

for d in "${seen[@]}"; do
  [[ -d $d ]] || deny "can't tell where this commit runs ($d is not a directory here). Commit with a literal path: cd <dir> && git commit ..."
  b=$(branch_of "$d")
  if protected "$b"; then deny_branch "$b"; fi
done
