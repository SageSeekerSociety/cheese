import type { TaskProgress } from '@/types'

import { ref } from 'vue'

import { TaskBoardApi } from '@/api/taskBoard'

/**
 * 这道题每个阶段上的进度（`GET /tasks/{id}/progress`）：我的那一份，和批改的人才有的
 * 全体分布。读不到就是 `null`，画面上不画进度，不编一个出来。
 *
 * 题目页取一次，交给说明、我的提交、数据几个页签和右栏；有人交了、判了，题目页重读。
 */
export function useTaskProgress() {
  const progress = ref<TaskProgress | null>(null)

  async function load(taskId: number) {
    try {
      progress.value = await TaskBoardApi.progress(taskId)
    } catch {
      progress.value = null
    }
  }

  return { progress, load }
}
