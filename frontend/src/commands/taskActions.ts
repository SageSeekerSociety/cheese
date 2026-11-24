// 对一件任务能做什么。
//
// 侧栏任务行的右键（和手机上长按升起的面板）、手机任务页顶栏的 ⋯、编辑器那一种摆法
// 页头的 ⋯，列的是同一份，答案只在这里写一次：复制链接、重命名，分隔线，关闭任务（关了
// 的是重新打开）。换负责人、加协作者在「任务信息」里。
//
// 权限也在这里判，和后端同一条：改名、关闭归参加这件任务的人（负责人或协作者，
// `topics_title.py`、`topics_tasks.conclude_task`）；重新打开归角色有「新建任务和审阅」
// 的人（`Permission.tasks`，`topics_tasks.reopen_task`）——关闭会作废交付、关掉 PR，重开
// 不还原这些；重开能再关回去，频道里也写着是谁重开的，所以放得宽。
import type { MenuCommand } from '.'

import { t } from '@/i18n'

/** 这些操作要的那几格：`RoomTask` 和侧栏那一行的 `RailTask` 都带得全。 */
export interface TaskActionTask {
  id: string
  project_id: string
  room_id: string
  status: string
  owner_handle?: string | null
  contributor_handles?: readonly string[]
}

export interface TaskActionHandlers {
  /** 行内改还是开对话框由调用方决定。 */
  rename: () => void
  /** 关闭要先写一句结论（可以不写），那张确认框由调用方出。 */
  close: () => void
  reopen: () => void
}

export interface TaskViewer {
  /** 此刻是谁在看。 */
  me: string
  /** 我的角色有「新建任务和审阅」（项目的 `my_permissions` 里有 `tasks`）：关了的任务我能重新打开。 */
  worksTasks?: boolean
  /** 复制这件任务的链接。没有 router 的宿主（单测、组件预览）给 null，那一件就不画：
   *  算链接要 router，而这一份不许自己去读路由（组件那一层不认路由）。 */
  copyLink?: (() => void) | null
}

/** 我参不参加这件任务：负责人或协作者。 */
export function takesPartInTask(
  task: { owner_handle?: string | null; contributor_handles?: readonly string[] },
  me: string
): boolean {
  return task.owner_handle === me || (task.contributor_handles ?? []).includes(me)
}

/** 一件任务能做得到的事，按它此刻的样子（开着 / 关了、我是不是参与的人）。 */
export function taskActions(task: TaskActionTask, on: TaskActionHandlers, viewer: TaskViewer): MenuCommand[] {
  const actions: MenuCommand[] = []
  // 复制链接谁都能做：看得见这件任务的人就有它的地址。
  if (viewer.copyLink)
    actions.push({
      id: 'task.copyLink',
      title: t('work.room.menu.copyLink'),
      icon: 'mdi-link-variant',
      run: viewer.copyLink,
    })
  const takesPart = takesPartInTask(task, viewer.me)
  if (takesPart)
    actions.push({ id: 'task.rename', title: t('work.task.rename'), icon: 'mdi-pencil-outline', run: on.rename })
  const end =
    task.status === 'open'
      ? takesPart
        ? { id: 'task.close', title: t('work.task.close'), icon: 'mdi-check-circle-outline', run: on.close }
        : null
      : viewer.worksTasks
        ? { id: 'task.reopen', title: t('work.task.reopen'), icon: 'mdi-undo-variant', run: on.reopen }
        : null
  if (end) actions.push({ ...end, divider: actions.length > 0 })
  return actions
}
