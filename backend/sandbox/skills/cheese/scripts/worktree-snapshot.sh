#!/usr/bin/env bash
#
# 把当前这一步的活落到远端，换机后还能接回来。
#
# 为什么要有它：跑会话的机器可能被无预警回收，`$HOME` 一起清掉，平台看不见的
# 东西就是丢了。这里管两种情况：
#
#   1. 任务分支没到远端，或者这一轮压根没跑过 `cheese sync`；
#   2. 东西写在任务目录以外——笔记、产物、房间文件——没有任何备份覆盖它。
#
# 它自己不实现快照。`cheese sync` 已经会备份任务目录（改过的文件、新文件，连
# 没提交的也算）并把提交推到任务分支；`cheese worktree <任务 id>` 在新环境里第
# 一次建目录时把那份备份恢复回来。在这里再写一份，等于给同一件事留两份会分叉的
# 实现。它做的是跑平台自己的那条路，然后当场核对——要防的正是「没看就说存好了」。
#
# 用法：
#   worktree-snapshot.sh save                 当前任务目录：同步并核对远端（默认）
#   worktree-snapshot.sh restore [--task ID]  换机后接回来：恢复并核对
#
# 退出码：
#   0  核对通过
#   1  依赖或 git 命令失败（对方说什么原样打出来）
#   2  拒判：不在任务目录里，或分支状态没法判断
#   3  没能确认落到远端
#
set -euo pipefail

PROG=$(basename "$0")
SINCE_MIN=${SINCE_MIN:-180}
MAX_LISTED=${MAX_LISTED:-20}

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed -e 's/^#\{1,2\} \{0,1\}//' -e '$d'
}

die() {
  local code=$1
  shift
  printf '%s: %s\n' "$PROG" "$*" >&2
  exit "$code"
}

checked_out_branch() {
  git symbolic-ref --quiet --short HEAD 2>/dev/null
}

remote_tip() {
  local out
  REMOTE_OK=yes
  if ! out=$(git ls-remote origin "refs/heads/$1" 2>&1); then
    REMOTE_OK=no
    REMOTE_TIP=""
    return 0
  fi
  REMOTE_TIP=$(printf '%s\n' "$out" | head -n 1 | cut -f1)
}

dirty_count() {
  git status --porcelain 2>/dev/null | wc -l | tr -d ' '
}

# 任务目录以外的、最近动过的文件。这些不在任何备份里。
list_outside() {
  local root=$1 dir
  for dir in "$HOME" ${TMPDIR:-} /tmp; do
    [ -d "$dir" ] || continue
    find "$dir" -mindepth 1 -maxdepth 3 \
      \( -name '.*' -prune \) -o \
      \( -type f -mmin -"$SINCE_MIN" -not -path "$root/*" -print \) 2>/dev/null || true
  done | sort -u
}

report_outside() {
  local root=$1 files count
  files=$(list_outside "$root")
  [ -n "$files" ] || return 0
  count=$(printf '%s\n' "$files" | wc -l | tr -d ' ')
  printf '  这些在任务目录以外，备份不覆盖，换机后回不来（挪进任务目录提交，或 cheese show 摆出来）：\n'
  printf '%s\n' "$files" | head -n "$MAX_LISTED" | sed 's/^/    /'
  if [ "$count" -gt "$MAX_LISTED" ]; then
    printf '    （%s 个里只列了前 %s 个）\n' "$count" "$MAX_LISTED"
  fi
}

shorts() {
  [ -n "$1" ] && printf '%s' "${1:0:8}" || printf '（空）'
}

cannot_confirm() {
  local branch=$1 task=$2 why=$3
  printf '  没能确认远端有这个提交：%s\n' "$why" >&2
  printf '  远端现在是什么：git ls-remote origin refs/heads/%s\n' "$branch" >&2
  printf '  重试：cheese sync；远端比你新就先合目标分支再同步；还不行就报告具体故障，不要说已存好。\n' >&2
  printf '  换机后接回来：cheese worktree %s（新环境里第一次建目录时恢复最近一次备份）\n' "$task" >&2
  exit 3
}

if [ $# -gt 0 ]; then
  case $1 in
    -h|--help) usage; exit 0 ;;
  esac
fi

sub=save
task_opt=""
while [ $# -gt 0 ]; do
  case $1 in
    save|restore) sub=$1 ;;
    --task) shift; task_opt=${1:-} ;;
    --task=*) task_opt=${1#--task=} ;;
    -h|--help) usage; exit 0 ;;
    *) die 2 "不认识的参数：$1（-h 看用法）" ;;
  esac
  shift
done

command -v cheese >/dev/null 2>&1 ||
  die 1 "这台机器上没有 cheese 命令，跑不了（会话里正常会有的）"

if [ "$sub" = save ]; then
  root=$(git rev-parse --show-toplevel 2>/dev/null) ||
    die 2 "当前目录不在 git 仓库里。进任务目录再跑：cheese worktree <任务 id>"
  branch=$(checked_out_branch) ||
    die 2 "当前是分离头指针，分不出是哪条任务。先 git switch task/<任务 id>，或在任务目录里跑"
  case $branch in
    task/*) ;;
    *) die 2 "当前分支是 $branch，不是任务分支。任务目录里的分支叫 task/<任务 id>：cheese worktree <任务 id>" ;;
  esac
  task_id=${branch#task/}

  printf '%s: 同步 %s\n' "$PROG" "$branch"
  sync_out=$(cheese sync 2>&1) || {
    printf '%s\n' "$sync_out"
    die 1 "cheese sync 失败（上面是它说的）。这一步没落到远端"
  }
  if [ -n "$sync_out" ]; then printf '%s\n' "$sync_out"; fi

  head_sha=$(git rev-parse HEAD)
  remote_tip "$branch"
  if [ "$REMOTE_OK" != yes ]; then
    cannot_confirm "$branch" "$task_id" "取不到远端 refs（连不上远端，或者凭据不能用）"
  fi
  if [ -z "$REMOTE_TIP" ]; then
    cannot_confirm "$branch" "$task_id" "远端 refs/heads/$branch 上还没有任何提交"
  elif [ "$REMOTE_TIP" != "$head_sha" ]; then
    cannot_confirm "$branch" "$task_id" "远端是 $(shorts "$REMOTE_TIP")，本地 HEAD 是 $(shorts "$head_sha")"
  fi

  printf '远端 refs/heads/%s 就是 %s\n' "$branch" "${head_sha:0:8}"
  printf '换机后接回来：cheese worktree %s\n' "$task_id"
  report_outside "$root"
  exit 0
fi

# restore：新环境里从远端把这些东西接回来
if [ -n "$task_opt" ]; then
  task_id=$task_opt
else
  branch=$(checked_out_branch) || branch=""
  case $branch in
    task/*) task_id=${branch#task/} ;;
    *) die 2 "没说是哪条任务。用 --task <任务 id>，或在任务目录里跑" ;;
  esac
fi

printf '%s: cheese worktree %s\n' "$PROG" "$task_id"
worktree_out=$(cheese worktree "$task_id" 2>&1) || {
  printf '%s\n' "$worktree_out"
  die 1 "cheese worktree $task_id 失败（上面是它说的）"
}
  if [ -n "$worktree_out" ]; then printf '%s\n' "$worktree_out"; fi

root=$(printf '%s\n' "$worktree_out" | grep '^/' | tail -n 1)
[ -n "$root" ] && [ -d "$root" ] ||
  die 2 "没从 cheese worktree 的输出里认出工作目录在哪，它说的是上面那些"

cd "$root"
branch=$(checked_out_branch) || die 2 "$root 里是分离头指针，接回来的状态不对"
head_sha=$(git rev-parse HEAD)
remote_tip "$branch"
[ "$REMOTE_OK" = yes ] ||
  cannot_confirm "$branch" "$task_id" "取不到远端 refs（连不上远端，或者凭据不能用）"
[ -n "$REMOTE_TIP" ] && [ "$REMOTE_TIP" = "$head_sha" ] ||
  cannot_confirm "$branch" "$task_id" "远端是 $(shorts "$REMOTE_TIP")，本地 HEAD 是 $(shorts "$head_sha")"

printf '工作目录 %s\n' "$root"
printf '分支 %s，HEAD %s，与远端一致\n' "$branch" "${head_sha:0:8}"
printf '工作区有 %s 个文件是恢复回来的未提交改动\n' "$(dirty_count)"
printf '回不来的：依赖与缓存、生成目录、任务目录以外的文件（含 /tmp）、当时在跑的进程。\n'
exit 0
