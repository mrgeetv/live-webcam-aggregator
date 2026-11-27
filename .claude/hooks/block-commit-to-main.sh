#!/bin/bash
# PreToolUse hook: blocks `git commit` on main, master or next, judged where the
# commit runs (worktrees included). Registered on every Bash call in
# .claude/settings.json; it decides here because a settings `if` fails open on
# compound commands.

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
# Parse `git checkout|switch`: NEW = branch it creates, POS = other words.
parse_switch() {
  local -a w
  read -ra w <<<"$1"
  NEW="" POS=()
  local j
  for ((j = 2; j < ${#w[@]}; j++)); do
    case ${w[j]} in
      -b | -B | -c | -C | --orphan) NEW=${w[j + 1]:-}; j=$((j + 1)) ;;
      --) POS=(); return ;;
      -) POS+=(-) ;;
      -*) ;;
      *) POS+=("${w[j]}") ;;
    esac
  done
}
deny_branch() {
  deny "direct commit to protected branch \"$1\". Per project CLAUDE.md, create a feature branch first: git checkout -b fix/your-change"
}

# Where the commit runs: trusted only for plain `cd <dir>` / SAFE-command chains;
# otherwise every directory involved is checked, and any protected one denies.
SAFE=" git uv echo printf true ls cat gh python3 jq grep sed head tail wc cut sort mkdir cp mv touch test "
GIT_OPTS=" --no-pager -P --paginate --no-optional-locks "
LIT='("[^"$`\\]*"|'"'"'[^'"'"']*'"'"'|[^[:space:]"'"'"'$`\\]+)'
project=${CLAUDE_PROJECT_DIR:-.}
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[[ -n $cwd ]] || cwd=$project
# Everything before the commit (its heredoc message excluded); the x keeps a
# trailing newline.
before=$(printf '%s' "$cmd" | sed -zE 's/(^|[[:space:];&|(])git[[:space:]]([^;&|\n]*[[:space:]])?commit\b.*/\1/' | tr -d '\0'; printf x)
before=${before%x}
commit=$(printf '%s' "$cmd" | grep -oE '(^|[[:space:];&|(])git[[:space:]]([^;&|]*[[:space:]])?commit\b' | head -n1 || true)
commits=$(printf '%s' "$cmd" | grep -oE '(^|[[:space:];&|(])git[[:space:]]([^;&|]*[[:space:]])?commit\b' | wc -l)

# GIT_DIR and friends can't be followed: deny.
scan=$before$commit
((commits <= 1)) || scan=$cmd
if printf '%s' "$scan" | grep -Eq 'GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR|--git-dir|--work-tree|core\.worktree'; then
  deny "this commit names its repository through GIT_DIR, --git-dir, --work-tree or core.worktree, which this hook can't check. Commit from the worktree's own directory instead."
fi
# Checkout onto a protected branch: deny.
while read -r sw; do
  sw=${sw#"${sw%%git*}"}
  parse_switch "$sw"
  if [[ -n $NEW ]]; then
    if protected "$NEW"; then deny_branch "$NEW"; fi
  else
    for tok in "${POS[@]}"; do
      if protected "$tok"; then deny_branch "$tok"; fi
      [[ $tok == - ]] && deny "can't tell which branch \`git checkout -\` lands on. Commit in a separate command."
    done
  fi
done < <(printf '%s' "$before" | grep -oE '(^|[[:space:];&|(])git[[:space:]]+(checkout|switch)([[:space:]][^;&|]*)?' || true)

certain=true
# More than one commit: unsure.
((commits <= 1)) || certain=false
dir=$cwd
override=""
seen=("$cwd" "$project")
joined=${before//&&/;}
if [[ $joined == *[\|\&\(\)\{\}\$\`\<\>\#\\]* ]]; then
  certain=false
else
  # Quotes only around a cd's dir.
  quotes=$(printf '%s' "$joined" | tr -cd "\"'" | wc -c)
  mapfile -t segs < <(printf '%s' "$joined" | tr ';\n' '\n\n')
  for seg in "${segs[@]}"; do
    seg=$(printf '%s' "$seg" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')
    [[ -z $seg ]] && continue
    if [[ $seg =~ ^cd[[:space:]]+$LIT$ ]]; then
      dir=$(resolve "$dir" "$(unquote "${BASH_REMATCH[1]}")")
      seen+=("$dir")
      override=""
      quotes=$((quotes - $(printf '%s' "$seg" | tr -cd "\"'" | wc -c)))
    elif [[ $seg =~ ^git[[:space:]]+(checkout|switch)([[:space:]]|$) ]]; then
      parse_switch "$seg"
      if [[ -n $NEW ]]; then
        override=$NEW
      elif ((${#POS[@]} == 1)) && git -C "$dir" show-ref --verify --quiet "refs/heads/${POS[0]}"; then
        override=${POS[0]}
      fi
    elif [[ $seg =~ ^git[[:space:]] && $seg =~ [[:space:]](checkout|switch)([[:space:]]|$) ]]; then
      certain=false
    elif [[ $SAFE != *" ${seg%%[[:space:]]*} "* ]]; then
      certain=false
    fi
  done
  ((quotes == 0)) || certain=false
  # A failed checkout stops the commit only under &&.
  [[ $before == *";"* || $before == *$'\n'* ]] && override=""
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
      override="" # a checkout ran elsewhere
      seen+=("$dir")
      i=$((i + 2))
      continue
    fi
    [[ $GIT_OPTS == *" ${toks[i]} "* ]] || certain=false
    i=$((i + 1))
  done
fi
if ! $certain; then
  # Unsure: check every cd and -C target too.
  mapfile -t more < <(printf '%s' "$cmd" | grep -oE '(cd|-C)[[:space:]]+[^[:space:];&|)]+' | sed -E 's/^(cd|-C)[[:space:]]+//' || true)
  for t in "${more[@]}"; do seen+=("$(resolve "$cwd" "$(unquote "$t")")"); done
fi

if $certain && [[ -d $dir ]]; then
  branch=${override:-$(branch_of "$dir")}
  # Not a repo: fall back to the project's branch.
  [[ -n $branch ]] || branch=$(branch_of "$project")
  if protected "$branch"; then deny_branch "$branch"; fi
  exit 0
fi

for d in "${seen[@]}"; do
  [[ -d $d ]] || deny "can't tell where this commit runs ($d is not a directory here). Commit with a literal path: cd <dir> && git commit ..."
  b=$(branch_of "$d")
  if protected "$b"; then deny_branch "$b"; fi
done
