import type { TaskFileData } from '@/types/taskFiles'

import { ref } from 'vue'

import { downloadFile } from '@/api'
import { listTaskFiles, taskFileRawUrl } from '@/api/taskFiles'

/**
 * 一道题的材料清单（题目资料库里出题人放进来的文件）。
 *
 * 清单本身对**看得见这道题的人**都可见：看不见材料就无从判断要不要领这道题。能不能
 * 下载是另一回事，由服务端在 `canDownload` 里给 —— 出题人、空间管理员、已经领取的人
 * 拿得到，其余人那一行写「领取这道题之后才能下载」。
 */
export function useTaskAttachments() {
  const attachments = ref<TaskFileData[]>([])
  const canDownload = ref(false)
  /** 正在下载的那一份（名字）。 */
  const downloading = ref<string | null>(null)

  async function load(taskId: number | null | undefined) {
    if (!taskId) {
      attachments.value = []
      canDownload.value = false
      return
    }
    try {
      const data = await listTaskFiles(taskId)
      attachments.value = data.files ?? []
      canDownload.value = data.canDownload ?? false
    } catch {
      // 清单取不到就整块不显示：一道没有材料的题和一次失败的请求在屏幕上长得一样，
      // 但把「加载失败」画成「没有附件」会让人以为材料不存在。这里选择不显示。
      attachments.value = []
      canDownload.value = false
    }
  }

  async function download(taskId: number, file: TaskFileData) {
    downloading.value = file.name
    try {
      await downloadFile(taskFileRawUrl(taskId, file.name), file.name)
    } finally {
      downloading.value = null
    }
  }

  return { attachments, canDownload, downloading, load, download }
}
