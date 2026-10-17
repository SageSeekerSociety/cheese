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
#   worktree-snapshot.sh save                     当前任务目录：同步并核对远端（默认）
#   worktree-snapshot.sh restore --task <任务 id> 换机后接回来：恢复并核对
#
# 退出码：
#   0  核对通过
#   1  依赖或 git 命令失败（对方说什么原样打出来）
#   2  拒判：不在任务目录里，或分支状态没法判断
#   3  没能确认：save 是没确认落到远端，restore 是没法核对接回来的状态
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

# 远端这条分支的顶端。取不到（连不上、凭据不能用）时 REMOTE_OK=no——这和
# 「远端还没有这个提交」是两回事，该跑的命令也不同。
remote_tip() {
  local out
  REMOTE_OK=yes
  if ! out=$(git ls-remote origin "refs/heads/$1" 2>&1); then
    REMOTE_OK=no
    REMOTE_TIP=""
    return 0
  fi
  REMOTE_TIP=$(printf '%s\n' "$out" | sed -n '1p' | cut -f1)
}

uuid_like() {
  case $1 in
    ????????-????-????-????-????????????) return 0 ;;
    *) return 1 ;;
  esac
}

# `cheese worktree` 只认完整 UUID。平台把任务目录建在 ~/.cheese/tasks/<uuid>，
# 目录名就是它；不在那个位置时退回会话里的 CHEESE_TOPIC。分支名里那 8 位
# （task/61525614）不是 id，拿它去恢复会被 CLI 当场拒掉。
task_id_of() {
  case $1 in
    */.cheese/tasks/*) basename "$1" ;;
    *) printf '%s' "${CHEESE_TOPIC:-}" ;;
  esac
}

dirty_count() {
  git status --porcelain 2>/dev/null | wc -l | tr -d ' '
}

# $HOME 下最近动过、又不在任务目录里的文件。备份只覆盖任务目录，这些换机就没了。
# 只看 $HOME：/tmp 是全机共用的，一次能扫出几百个别的进程留下的东西，列出来只会
# 把真该提醒的那几条挤掉。下面这些是平台、工具和依赖自己的状态（会话家的
# .cheese-environment 和 .claude 每次都在动），全都不是谁的活，列出来只会盖住
# 真正要提醒的那几条。
list_outside() {
  local root=$1 f
  find "$HOME" -mindepth 1 -maxdepth 3 \
    \( -name .cache -o -name .local -o -name .cheese -o -name .cheese-environment \
       -o -name .claude -o -name .config -o -name .npm \
       -o -name .cargo -o -name .rustup -o -name .nvm -o -name .venv \
       -o -name node_modules -o -name .git \) -prune \
    -o -type f -mmin -"$SINCE_MIN" -print 2>/dev/null |
  while IFS= read -r f; do
    case $f in
      "$root"/*) continue ;;
    esac
    printf '%s\n' "$f"
  done | sort -u
}

report_outside() {
  local root=$1 files count
  # 这是提醒，不是核对：扫不动（权限、没有 $HOME）不能把已经核对过的成功改成失败。
  files=$(list_outside "$root" || true)
  [ -n "$files" ] || return 0
  count=$(printf '%s\n' "$files" | wc -l | tr -d ' ')
  printf '  这些在 $HOME 里、任务目录以外，备份不覆盖，换机后回不来（挪进任务目录提交，或 cheese show 摆出来）：\n' >&2
  printf '%s\n' "$files" | sed -n "1,${MAX_LISTED}p" | sed 's/^/    /' >&2
  if [ "$count" -gt "$MAX_LISTED" ]; then
    printf '    （%s 个里只列了前 %s 个）\n' "$count" "$MAX_LISTED" >&2
  fi
}

shorts() {
  if [ -n "$1" ]; then printf '%s' "${1:0:8}"; else printf '（空）'; fi
}

# save 用：同步跑过了，远端却没有这个提交
cannot_confirm() {
  local branch=$1 reentry=$2 why=$3
  printf '  没能确认远端有这个提交：%s\n' "$why" >&2
  printf '  远端现在是什么：git ls-remote origin refs/heads/%s\n' "$branch" >&2
  printf '  重试：cheese sync；还不行就报告具体故障，不要说已存好。\n' >&2
  printf '  换机后接回来：%s（新环境里第一次建目录时恢复最近一次备份）\n' "$reentry" >&2
  exit 3
}

# restore 用：接回来的状态和远端对不上（落后，或者分叉）
restore_mismatch() {
  local branch=$1 why=$2
  printf '  接回来的状态和远端对不上：%s\n' "$why" >&2
  printf '  远端现在是什么：git ls-remote origin refs/heads/%s\n' "$branch" >&2
  printf '  先别在这个目录里接着改；把上面的输出原样报上来，说清没能确认恢复到了哪个提交。\n' >&2
  exit 3
}

# restore 用：没有东西可以拿来对照，说清是什么、该怎么办
restore_stop() {
  printf '  接回来的状态没法核对：%s\n' "$1" >&2
  printf '  %s\n' "$2" >&2
  exit 3
}

# 平台没能把快照套进工作区时，不覆盖任何东西，把它原样检出到
# ~/.cheese/recovered/<任务 id>-<快照>，只往 stderr 说一句，`cheese worktree` 的
# 退出码仍是 0（平台 backend/sandbox/cheese 的 _apply_snapshot 与 _recovery_dir）。
# 这次新出现这么一份，就是「东西没接回来」的凭据；早就在的旧目录不算。
snapshot_set_aside() {
  local task=$1 marker=$2 out
  out=$(find "$HOME/.cheese/recovered" -maxdepth 1 -name "$task-*" \
    -newer "$marker" 2>/dev/null) || true
  printf '%s' "${out%%$'\n'*}"
}

if [ $# -gt 0 ]; then
  case $1 in
    -h|--help) usage; exit 0 ;;
  esac
fi

sub=""
task_opt=""
while [ $# -gt 0 ]; do
  case $1 in
    save|restore)
      [ -z "$sub" ] || [ "$sub" = "$1" ] ||
        die 2 "一次只跑一个子命令：给的是 $sub 和 $1"
      sub=$1
      ;;
    --task)
      [ $# -ge 2 ] && [ -n "$2" ] ||
        die 2 "--task 后面要跟完整的任务 id（cheese worktree 只认完整 UUID）"
      task_opt=$2
      shift
      ;;
    --task=*)
      task_opt=${1#--task=}
      [ -n "$task_opt" ] ||
        die 2 "--task 后面要跟完整的任务 id（cheese worktree 只认完整 UUID）"
      ;;
    -h|--help) usage; exit 0 ;;
    *) die 2 "不认识的参数：$1（-h 看用法）" ;;
  esac
  shift
done
sub=${sub:-save}
if [ "$sub" = save ] && [ -n "$task_opt" ]; then
  die 2 "save 不接受 --task：它在当前任务目录里跑（换机恢复用 restore --task <任务 id>）"
fi

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
  task_id=$(task_id_of "$root")
  if uuid_like "$task_id"; then
    reentry="cheese worktree $task_id"
  else
    reentry="cheese worktree <任务 id>（完整 id 见任务卡；也可以在任务目录里用 CHEESE_TOPIC 再跑一次）"
  fi

  printf '%s: 同步 %s\n' "$PROG" "$branch"
  sync_out=$(cheese sync 2>&1) || {
    printf '%s\n' "$sync_out"
    die 1 "cheese sync 失败（上面是它说的）。这一步没落到远端"
  }
  if [ -n "$sync_out" ]; then printf '%s\n' "$sync_out"; fi

  # 多步任务每交一步换一条分支（task/<id>、task/<id>-2……），`cheese sync` 会把
  # 目录跟过去，推的是新那条。照着同步前的名字核对，推成功了也会说成失败。
  branch=$(checked_out_branch) ||
    die 2 "cheese sync 之后这里是分离头指针，分不出是哪条任务"
  case $branch in
    task/*) ;;
    *) die 2 "cheese sync 之后当前分支是 $branch，不是任务分支" ;;
  esac

  head_sha=$(git rev-parse HEAD)
  remote_tip "$branch"
  [ "$REMOTE_OK" = yes ] ||
    cannot_confirm "$branch" "$reentry" "取不到远端 refs（连不上远端，或者凭据不能用）"
  [ -n "$REMOTE_TIP" ] ||
    cannot_confirm "$branch" "$reentry" "远端 refs/heads/$branch 上还没有任何提交"

  if [ "$REMOTE_TIP" = "$head_sha" ]; then
    printf '远端 refs/heads/%s 就是 %s\n' "$branch" "${head_sha:0:8}"
  else
    # 任务分支可以有多个写入者（另一个 checkout、合并队列），远端走得更远是正常
    # 的。这里要确认的是「这个提交在远端的历史里」，不是「远端顶端正好等于它」。
    git fetch -q --no-tags origin "refs/heads/$branch" 2>/dev/null ||
      cannot_confirm "$branch" "$reentry" "远端顶端是 $(shorts "$REMOTE_TIP")，本地没有它的提交，又取不下来"
    fetched=$(git rev-parse --verify -q FETCH_HEAD 2>/dev/null) || fetched=""
    if [ -n "$fetched" ] && git merge-base --is-ancestor "$head_sha" "$fetched" 2>/dev/null; then
      printf '远端 refs/heads/%s 是 %s，包含本地的 %s（远端另有 %s 个提交，正常）\n' \
        "$branch" "${fetched:0:8}" "${head_sha:0:8}" \
        "$(git rev-list --count "$head_sha..$fetched")"
    else
      cannot_confirm "$branch" "$reentry" \
        "远端是 $(shorts "$REMOTE_TIP")，本地 HEAD 是 $(shorts "$head_sha")，远端没有这个提交"
    fi
  fi

  printf '换机后接回来：%s\n' "$reentry"
  report_outside "$root"
  exit 0
fi

# restore：新环境里从远端把这些东西接回来
if [ -n "$task_opt" ]; then
  task_id=$task_opt
else
  branch=$(checked_out_branch) || branch=""
  case $branch in
    task/*) task_id=$(task_id_of "$(git rev-parse --show-toplevel 2>/dev/null || true)") ;;
    *) task_id=${CHEESE_TOPIC:-} ;;
  esac
  [ -n "$task_id" ] ||
    die 2 "没说是哪条任务。用 --task <任务 id>，或在任务目录里跑"
fi
uuid_like "$task_id" ||
  die 2 "「$task_id」不是完整的任务 id；cheese worktree 只认完整 UUID。用 --task <任务 id> 再跑"

started=$(mktemp) || die 1 "建不了临时文件（mktemp）"
trap 'rm -f "$started"' EXIT

printf '%s: cheese worktree %s\n' "$PROG" "$task_id" >&2
worktree_out=$(cheese worktree "$task_id") ||
  die 1 "cheese worktree $task_id 失败（上面是它说的）"
if [ -n "$worktree_out" ]; then printf '%s\n' "$worktree_out"; fi

# 输出里最后一行以 / 开头的是工作目录；awk 读不到也返回 0，不会在 set -e 下提前退出
root=$(printf '%s\n' "$worktree_out" | awk '/^\// { p = $0 } END { if (p != "") print p }')
[ -n "$root" ] && [ -d "$root" ] ||
  die 2 "没从 cheese worktree 的输出里认出工作目录在哪，它说的是上面那些"

cd "$root"
branch=$(checked_out_branch) || die 2 "$root 里是分离头指针，接回来的状态不对"
head_sha=$(git rev-parse HEAD)

aside=$(snapshot_set_aside "$task_id" "$started")
if [ -n "$aside" ]; then
  restore_stop "平台有一份快照没套进这个目录，它原样放在 $aside" \
    "cheese worktree 的退出码是 0，只把这件事写在上面的 stderr 里：这里接回来的只有分支上已推送的内容。先在 $aside 里取要的东西，再跑一次这个命令会重试恢复。"
fi

remote_tip "$branch"
if [ "$REMOTE_OK" != yes ]; then
  printf '  远端现在是什么：git ls-remote origin refs/heads/%s\n' "$branch" >&2
  restore_stop "取不到远端 refs（连不上远端，或者凭据不能用）" \
    "先别在这个目录里接着改；把上面的输出原样报上来，说清没能核对到哪个提交。"
fi

if [ -z "$REMOTE_TIP" ]; then
  # 推送失败或中断过：分支只在平台快照里。这是脚本要救的那种状态，不是失败。
  printf '工作目录 %s\n' "$root"
  printf '分支 %s，HEAD %s\n' "$branch" "${head_sha:0:8}"
  printf '远端 refs/heads/%s 上还没有提交：这条分支没推上去过（或者上次推送中断了）。\n' "$branch"
  printf '这一份是平台快照接回来的，没有远端可以对照；先跑 save 推上去，别等下一次换机。\n'
  printf '工作区没提交的文件：%s 个\n' "$(dirty_count)"
  printf '回不来的：依赖与缓存、生成目录、任务目录以外的文件（含 /tmp）、当时在跑的进程。\n'
  exit 0
fi

# 恢复会把备份里没推上去的提交快进回分支，所以本地比远端多是正常的（脚本下面
# 会把还差多少说清楚）；落后或者分叉才是有问题。
ahead=0
if [ "$REMOTE_TIP" = "$head_sha" ]; then
  ahead=0
elif git merge-base --is-ancestor "$REMOTE_TIP" "$head_sha" 2>/dev/null; then
  ahead=$(git rev-list --count "$REMOTE_TIP..$head_sha")
else
  restore_mismatch "$branch" "远端是 $(shorts "$REMOTE_TIP")，本地 HEAD 是 $(shorts "$head_sha")，两者分叉或者本地落后"
fi

printf '工作目录 %s\n' "$root"
printf '分支 %s，HEAD %s\n' "$branch" "${head_sha:0:8}"
if [ "$ahead" -gt 0 ]; then
  printf '其中 %s 个提交还没推到远端（cheese sync 会推上去）\n' "$ahead"
else
  printf '与远端一致\n'
fi
printf '工作区没提交的文件：%s 个\n' "$(dirty_count)"
printf '回不来的：依赖与缓存、生成目录、任务目录以外的文件（含 /tmp）、当时在跑的进程。\n'
exit 0
