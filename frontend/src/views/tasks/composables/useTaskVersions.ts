import type { TaskVersionData, TaskVersionFileData } from '@/types/taskFiles'

import { ref } from 'vue'

import { downloadFile } from '@/api'
import { listTaskVersions, taskFileRawUrl } from '@/api/taskFiles'

/**
 * 一道题的历史版本（后端 `GET /tasks/{id}/versions`），新的在前。
 *
 * 看得见这道题的人都拿得到；各版的文件能不能下载由服务端在 `canDownload` 里给，和
 * 现在的材料是同一条判据。
 */
export function useTaskVersions() {
  const versions = ref<TaskVersionData[]>([])
  const canDownload = ref(false)
  const loading = ref(false)
  /** 读失败的原因；`null` 是没失败。 */
  const error = ref<unknown>(null)
  /** 正在下载的那一份：哪一版的哪个名字。 */
  const downloading = ref<{ version: number; name: string } | null>(null)

  async function load(taskId: number | null | undefined) {
    if (!taskId) return
    loading.value = true
    error.value = null
    try {
      const data = await listTaskVersions(taskId)
      versions.value = data.versions ?? []
      canDownload.value = data.canDownload ?? false
    } catch (e) {
      error.value = e
    } finally {
      loading.value = false
    }
  }

  async function download(taskId: number, version: number, file: TaskVersionFileData) {
    if (file.size === null) return
    downloading.value = { version, name: file.name }
    try {
      await downloadFile(taskFileRawUrl(taskId, file.name, version), file.name)
    } finally {
      downloading.value = null
    }
  }

  return { versions, canDownload, loading, error, downloading, load, download }
}
