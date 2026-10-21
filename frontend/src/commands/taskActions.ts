// 对一件任务能做什么。
//
// 任务页右上角那颗 ⋯ 和侧栏任务行的 ⋯ 问的是同一个问题，答案只在这里写一次：一处加
// 一件能做的事，两处同时有。各处的差别只有「露出哪几件」——`taskActions` 是全部，
// `taskRowActions` 是侧栏那一份（只留扫列表时敢点的两件）。
//
// 权限也在这里判：改名归参加这件活的人（负责人或协作者，和后端 `topics_title.py` 同
// 一条），转交、关闭、重新打开只归负责人。
import type { Router } from 'vue-router'
import type { MenuCommand } from '.'

import { copyLink, linkOf } from './copy'

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
}

/** 我参不参加这件活：负责人或协作者。 */
export function takesPartInTask(
  task: { owner_handle?: string | null; contributor_handles?: readonly string[] },
  me: string
): boolean {
  return task.owner_handle === me || (task.contributor_handles ?? []).includes(me)
}

/** 一件任务能做得到的全部事，按它此刻的样子（开着 / 关了、我是不是参与的人）。 */
export function taskActions(
  task: TaskActionTask,
  router: Router | null,
  on: TaskActionHandlers,
  viewer: TaskViewer
): MenuCommand[] {
  const actions: MenuCommand[] = []
  if (takesPartInTask(task, viewer.me))
    actions.push({ id: 'task.rename', title: t('work.task.rename'), icon: 'mdi-pencil-outline', run: on.rename })
  // 复制链接谁都能做：看得见这件活的人就有它的地址。没有 router 的宿主（单测、组件
  // 预览）不算链接，那一件就不画。
  if (router)
    actions.push({
      id: 'task.copyLink',
      title: t('work.room.menu.copyLink'),
      icon: 'mdi-link-variant',
      run: () =>
        void copyLink(
          linkOf(router, {
            name: 'workspace-task',
            params: { projectId: task.project_id, topicId: task.room_id, taskId: task.id },
          })
        ),
    })
  if (task.owner_handle !== viewer.me) return actions
  if (task.status === 'open') {
    if (viewer.canHandOver)
      actions.push({
        id: 'task.handOver',
        title: t('work.task.handOver'),
        icon: 'mdi-account-switch-outline',
        run: on.handOver,
      })
    actions.push({ id: 'task.close', title: t('work.task.close'), icon: 'mdi-check-circle-outline', run: on.close })
    return actions
  }
  actions.push({ id: 'task.reopen', title: t('work.task.reopen'), icon: 'mdi-undo-variant', run: on.reopen })
  return actions
}

/** 侧栏任务行那一份：只留扫列表时敢点的。转交和关闭要弹窗、要看任务现状，留在任务页。 */
const ROW_ACTION_IDS = new Set(['task.rename', 'task.copyLink'])

export function taskRowActions(
  task: TaskActionTask,
  router: Router | null,
  on: Pick<TaskActionHandlers, 'rename'>,
  me: string
): MenuCommand[] {
  const full = taskActions(
    task,
    router,
    { ...on, handOver: () => {}, close: () => {}, reopen: () => {} },
    { me, canHandOver: false }
  )
  return full.filter((action) => ROW_ACTION_IDS.has(action.id))
}
