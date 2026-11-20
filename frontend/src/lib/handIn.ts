// 成员自己提交审阅时页头上给什么（后端 `machine.hand_in`）。
//
// 一件任务里保存过的修改只能作为交付的一版进项目。页头据此说三件事：有没有「有未提交
// 的修改」，给「提交审阅」还是「再提交一版」，以及采纳给不给——一版在等审阅、而有人
// 保存过还没交进去的修改时，那一版里没有这些修改，采纳它就把它们丢了，所以先再提交
// 一版。

import { ref } from 'vue'

export type HandInAction = 'submit' | 'resubmit' | null

export interface HandInFacts {
  /** 任务有不在最近那一版里的修改。 */
  unhanded: boolean
  /** 有一版在等审阅。 */
  waiting: boolean
}

/** 页头上的那颗提交按钮：没有要交的就不给。 */
export function handInAction({ unhanded, waiting }: HandInFacts): HandInAction {
  if (!unhanded) return null
  return waiting ? 'resubmit' : 'submit'
}

/** 在等审阅的那一版能不能采纳：有保存过、还没交进去的修改时不能。 */
export function acceptOffered({ unhanded, waiting }: HandInFacts): boolean {
  return !(waiting && unhanded)
}

/** 面板的主按钮：审阅的人就是自己时交了就采纳。 */
export function submitsAndAdopts(reviewer: string | null, me: string): boolean {
  return !!reviewer && reviewer === me
}

// 刚保存过的任务。平台记下「有没提交的修改」要在保存后十秒左右，这期间页头照这里说，
// 不让刚存过的人看到一个还没有的状态。交上去之后清掉。
const SAVED_FRESH_MS = 60_000
const savedAt = ref<Record<string, number>>({})

/** 这件任务刚在「改动」里存过一次。 */
export function noteSaved(taskId: string, now = Date.now()): void {
  savedAt.value = { ...savedAt.value, [taskId]: now }
}

/** 交上去了：刚存过的那一次已经在这一版里。 */
export function noteHandedIn(taskId: string): void {
  const rest = { ...savedAt.value }
  delete rest[taskId]
  savedAt.value = rest
}

/** 这件任务是不是刚存过（读的是响应式的记录，放进 computed 里会跟着变）。 */
export function savedRecently(taskId: string, now = Date.now()): boolean {
  const at = savedAt.value[taskId]
  return at !== undefined && now - at < SAVED_FRESH_MS
}
