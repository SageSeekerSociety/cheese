// 频道里点一枚文件 chip，右侧开的那一格：项目当前版本里的这份文件，只读。
//
// 频道里没有人改项目的文件——支线里的 AI 队友只能读，改东西的事在任务里做——所以
// 频道里提到的一份文件，说的就是项目现在的样子。当前版本里没有它时（某件任务新建的、
// 还没合进项目），再看这个频道里哪些任务改过它，好让人进那件任务去看。
import type { RoomTask } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { ApiError, downloadFile, getGitDiff, listRoomTasks, readFile, workspaceFileRawUrl } from '../api'
import { splitDiffByFile } from '../lib/diff'
import { useDocumentBytes } from '../lib/documentBytes'
import { DOCUMENT_TYPES, needsDocumentView, suffixOf } from '../lib/fileKind'

import { t } from '@/i18n'

const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp', 'avif'])

export function useProjectFile(opts: {
  projectId: () => string | null
  /** 点 chip 的那个频道。 */
  channelId: () => string | null
  path: () => string
}) {
  const loading = ref(true)
  const error = ref<string | null>(null)
  const missing = ref(false)
  const content = ref('')
  const version = ref<string | null>(null)
  const bytes = ref(0)
  const binary = ref(false)
  const tooLarge = ref(false)
  /** 当前版本里没有这份文件时，这个频道里改过它的任务。 */
  const tasks = ref<RoomTask[]>([])

  const isImage = computed(() => IMAGE_EXT.has(suffixOf(opts.path())))
  const isDocument = computed(() => needsDocumentView(opts.path()))
  const documentType = computed(() => DOCUMENT_TYPES[suffixOf(opts.path())] ?? null)
  const rawUrl = computed(() => {
    const pid = opts.projectId()
    return pid ? workspaceFileRawUrl(pid, opts.path(), opts.channelId() ?? undefined, null, 'committed') : ''
  })
  const doc = useDocumentBytes({
    topicId: () => opts.channelId(),
    path: () => opts.path(),
    version: () => version.value,
    task: () => null,
    source: () => 'committed',
    enabled: () => isDocument.value && !missing.value && !loading.value,
  })

  let request = 0
  async function load() {
    const pid = opts.projectId()
    const path = opts.path()
    const mine = ++request
    if (!pid) return
    loading.value = true
    error.value = null
    missing.value = false
    tasks.value = []
    try {
      const file = await readFile(pid, path, opts.channelId(), null, 'committed')
      if (mine !== request) return
      content.value = file.content ?? ''
      version.value = file.version
      bytes.value = file.bytes
      binary.value = file.binary
      tooLarge.value = file.too_large
    } catch (e) {
      if (mine !== request) return
      if (e instanceof ApiError && e.status === 404) {
        missing.value = true
        void findTasks(pid, path, mine)
      } else {
        error.value = e instanceof Error ? e.message : t('work.room.changes.readFileFailed')
      }
    } finally {
      if (mine === request) loading.value = false
    }
  }

  async function findTasks(pid: string, path: string, mine: number) {
    const channel = opts.channelId()
    if (!channel) return
    try {
      const listed = (await listRoomTasks(channel, { limit: 0, branch: true })).data
      const touched = await Promise.all(
        listed.map(async (task) => {
          try {
            const { diff } = await getGitDiff(pid, channel, task.id)
            return splitDiffByFile(diff).some((file) => file.path === path) ? task : null
          } catch {
            return null
          }
        })
      )
      if (mine === request) tasks.value = touched.flatMap((task) => (task ? [task] : []))
    } catch {
      // 只是给一条去路；找不到就只说当前版本里没有这份文件。
    }
  }

  async function download() {
    try {
      await downloadFile(rawUrl.value, opts.path().split('/').pop() || 'file')
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.room.changes.downloadFailed')
    }
  }

  watch(() => [opts.projectId(), opts.path()], load, { immediate: true })

  return {
    loading,
    error,
    missing,
    content,
    bytes,
    binary,
    tooLarge,
    tasks,
    isImage,
    isDocument,
    documentType,
    rawUrl,
    docBytes: doc.bytes,
    docLoading: doc.loading,
    docError: doc.error,
    docRendererMissing: doc.rendererMissing,
    download,
  }
}
