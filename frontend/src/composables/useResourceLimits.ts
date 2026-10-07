// 「这个账号能同时跑几个任务」那一行事实：请求与失败态都落在这里；
// `components/ResourceLimitsNotice.vue` 只画收到的状态、点这里给的回调——组件不吃
// API 层（.claude/rules/architecture.md）。
import type { ResourceLimits } from '@/api'

import { ref } from 'vue'

import { getResourceLimits } from '@/api'

export function useResourceLimits() {
  const limits = ref<ResourceLimits | null>(null)
  /** 拿不到和还没拿到是两句不同的话，调用方要分得开，所以失败单独记一笔。 */
  const failed = ref(false)

  /** 再取一次；重试那颗按钮走的就是它。 */
  async function load(): Promise<void> {
    failed.value = false
    try {
      limits.value = await getResourceLimits()
    } catch {
      failed.value = true
    }
  }

  return { limits, failed, load }
}
