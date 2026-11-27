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
# in a worktree is not the project's. ONE parser both decides whether the hook
# is CERTAIN of that directory and computes it, so the two can't disagree:
# everything before the commit must be segments joined by && or ; (no pipes, no
# lone &, which run a cd in a subshell; no `$(...)`, backticks, brackets,
# braces or redirects), each either exactly `cd <literal dir>`, which moves the
# directory, or a command from SAFE, which can't move the shell. The commit's
# own git options may only be unquoted `-C <dir>` (applied in order) and the
# harmless ones in GIT_OPTS. Then the directory starts at the hook input's .cwd
# and the cds and -Cs move it. Anything else leaves the hook UNSURE, and it
# checks every directory it can find (cwd, project, every cd and -C target):
# any on a protected branch, or any that doesn't resolve, denies.
SAFE=" git uv echo printf true ls cat gh python3 jq grep sed head tail wc cut sort mkdir cp mv touch test "
GIT_OPTS=" --no-pager -P --paginate --no-optional-locks "
LIT='("[^"$`\\]*"|'"'"'[^'"'"']*'"'"'|[^[:space:]"'"'"'$`\\]+)'
project=${CLAUDE_PROJECT_DIR:-.}
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[[ -n $cwd ]] || cwd=$project
# -z: the whole command is one record, so a heredoc's lines after the commit
# (its message) are cut away with it instead of read as commands before it.
before=$(printf '%s' "$cmd" | sed -zE 's/\bgit\b[^;&|]*[[:space:]]commit\b.*//' | tr -d '\0')
commit=$(printf '%s' "$cmd" | grep -oE '\bgit\b[^;&|]*[[:space:]]commit\b' | head -n1 || true)

certain=true
dir=$cwd
seen=("$cwd" "$project")
joined=${before//&&/;}
if [[ $joined == *[\|\&\(\)\{\}\$\`\<\>]* ]]; then
  certain=false
else
  mapfile -t segs < <(printf '%s' "$joined" | tr ';\n' '\n\n')
  for seg in "${segs[@]}"; do
    seg=$(printf '%s' "$seg" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')
    [[ -z $seg ]] && continue
    if [[ $seg =~ ^cd[[:space:]]+$LIT$ ]]; then
      dir=$(resolve "$dir" "$(unquote "${BASH_REMATCH[1]}")")
      seen+=("$dir")
    elif [[ $SAFE != *" ${seg%%[[:space:]]*} "* ]]; then
      certain=false
    fi
  done
fi
# The commit's git options, token by token up to `commit`.
if [[ $commit == *[\"\'\\]* ]]; then
  certain=false
else
  read -ra toks <<<"$commit"
  i=1
  while ((i < ${#toks[@]})) && [[ ${toks[i]} != commit ]]; do
    if [[ ${toks[i]} == -C ]] && ((i + 1 < ${#toks[@]})); then
      dir=$(resolve "$dir" "${toks[i + 1]}")
      seen+=("$dir")
      i=$((i + 2))
      continue
    fi
    [[ $GIT_OPTS == *" ${toks[i]} "* ]] || certain=false
    i=$((i + 1))
  done
fi
if ! $certain; then
  # Over-collect: every cd and -C target anywhere before the commit is checked.
  mapfile -t more < <(printf '%s' "$before$commit" | grep -oE '(cd|-C)[[:space:]]+[^[:space:];&|)]+' | sed -E 's/^(cd|-C)[[:space:]]+//' || true)
  for t in "${more[@]}"; do seen+=("$(resolve "$cwd" "$(unquote "$t")")"); done
fi

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
