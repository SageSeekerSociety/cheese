import type { TaskAttachmentData } from '@/network/api/tasks/types'

import { ref } from 'vue'

import { downloadFile, taskAttachmentRawUrl } from '@/api'
import { TasksApi } from '@/network/api/tasks'

/**
 * 一道题的材料清单。
 *
 * 清单本身对**看得见这道题的人**都可见：看不见材料就无从判断要不要领这道题。能不能
 * 下载是另一回事，由服务端在 `canDownload` 里给 —— 出题人、空间管理员、已经领取的人
 * 拿得到，其余人那一行写「领取这道题之后才能下载」。
 */
export function useTaskAttachments() {
  const attachments = ref<TaskAttachmentData[]>([])
  const canDownload = ref(false)
  const downloadingId = ref<number | null>(null)

  async function load(taskId: number | null | undefined) {
    if (!taskId) {
      attachments.value = []
      canDownload.value = false
      return
    }
    try {
      const { data } = await TasksApi.listAttachments(taskId)
      attachments.value = data.attachments ?? []
      canDownload.value = data.canDownload ?? false
    } catch {
      // 清单取不到就整块不显示：一道没有材料的题和一次失败的请求在屏幕上长得一样，
      // 但把「加载失败」画成「没有附件」会让人以为材料不存在。这里选择不显示。
      attachments.value = []
      canDownload.value = false
    }
  }

  async function download(taskId: number, file: TaskAttachmentData) {
    downloadingId.value = file.id
    try {
      await downloadFile(taskAttachmentRawUrl(taskId, file.id), file.name)
      // 下载计数由服务端在真的取到字节之后 +1，这里跟着走一格，不重新拉整张清单。
      file.downloadCount += 1
    } finally {
      downloadingId.value = null
    }
  }

  return { attachments, canDownload, downloadingId, load, download }
}
