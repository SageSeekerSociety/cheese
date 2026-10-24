// 对一件任务能做什么。
//
// 任务页右上角那颗 ⋯ 和侧栏任务行的 ⋯ 问的是同一个问题，答案只在这里写一次：一处加
// 一件能做的事，两处同时有。各处的差别只有「露出哪几件」——`taskActions` 是全部，
// `taskRowActions` 是侧栏那一份（只留扫列表时敢点的两件）。
//
// 权限也在这里判，和后端同一条：改名、关闭归参加这件活的人（负责人或协作者，
// `topics_title.py`、`topics_tasks.conclude_task`），转交只归负责人；重新打开归角色有
// 「新建任务和审阅」的人（`Permission.tasks`，`topics_tasks.reopen_task`）——关闭会作废
// 卡、关掉 PR，重开不还原这些；重开能再关回去，房间里也写着是谁重开的，所以放得宽。
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
  handOver: () => void
  close: () => void
  reopen: () => void
}

export interface TaskViewer {
  /** 此刻是谁在看。 */
  me: string
  /** 这件活里还有别人可以接（负责人不能转给自己）。 */
  canHandOver: boolean
  /** 我的角色有「新建任务和审阅」（项目的 `my_permissions` 里有 `tasks`）：关了的任务我能重新打开。 */
  worksTasks?: boolean
  /** 复制这件任务的链接。没有 router 的宿主（单测、组件预览）给 null，那一件就不画：
   *  算链接要 router，而这一份不许自己去读路由（组件那一层不认路由）。 */
  copyLink?: (() => void) | null
}

/** 我参不参加这件活：负责人或协作者。 */
export function takesPartInTask(
  task: { owner_handle?: string | null; contributor_handles?: readonly string[] },
  me: string
): boolean {
  return task.owner_handle === me || (task.contributor_handles ?? []).includes(me)
}

/** 一件任务能做得到的全部事，按它此刻的样子（开着 / 关了、我是不是参与的人）。 */
export function taskActions(task: TaskActionTask, on: TaskActionHandlers, viewer: TaskViewer): MenuCommand[] {
  const actions: MenuCommand[] = []
  if (takesPartInTask(task, viewer.me))
    actions.push({ id: 'task.rename', title: t('work.task.rename'), icon: 'mdi-pencil-outline', run: on.rename })
  // 复制链接谁都能做：看得见这件活的人就有它的地址。
  if (viewer.copyLink)
    actions.push({
      id: 'task.copyLink',
      title: t('work.room.menu.copyLink'),
      icon: 'mdi-link-variant',
      run: viewer.copyLink,
    })
  if (task.status !== 'open') {
    if (viewer.worksTasks)
      actions.push({ id: 'task.reopen', title: t('work.task.reopen'), icon: 'mdi-undo-variant', run: on.reopen })
    return actions
  }
  if (task.owner_handle === viewer.me && viewer.canHandOver)
    actions.push({
      id: 'task.handOver',
      title: t('work.task.handOver'),
      icon: 'mdi-account-switch-outline',
      run: on.handOver,
    })
  if (takesPartInTask(task, viewer.me))
    actions.push({ id: 'task.close', title: t('work.task.close'), icon: 'mdi-check-circle-outline', run: on.close })
  return actions
}

/** 侧栏任务行那一份：只留扫列表时敢点的。转交和关闭要弹窗、要看任务现状，留在任务页。 */
const ROW_ACTION_IDS = new Set(['task.rename', 'task.copyLink'])

export function taskRowActions(
  task: TaskActionTask,
  on: Pick<TaskActionHandlers, 'rename'>,
  viewer: Pick<TaskViewer, 'me' | 'copyLink'>
): MenuCommand[] {
  const full = taskActions(
    task,
    { ...on, handOver: () => {}, close: () => {}, reopen: () => {} },
    { me: viewer.me, canHandOver: false, copyLink: viewer.copyLink }
  )
  return full.filter((action) => ROW_ACTION_IDS.has(action.id))
}
