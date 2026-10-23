// 频道里点一枚文件 chip，右侧开的那一格：项目当前版本里的这份文件，只读；项目里读不
// 到时，是频道自己的那一份（见下）。
//
// 频道里没有人改项目的文件——支线里的 AI 队友只能读，改东西的事在任务里做——所以
// 频道里提到的一份文件，说的就是项目现在的样子。当前版本里没有它时（某件任务新建的、
// 还没合进项目），再看这个频道里哪些任务改过它，好让人进那件任务去看。
import type { FileContent, RoomTask } from '../cx_types'

import { computed, ref, watch } from 'vue'

import {
  ApiError,
  attachmentRawUrl,
  downloadFile,
  getGitDiff,
  listRoomTasks,
  readFile,
  readPreviewFile,
  workspaceFileRawUrl,
} from '../api'
import { splitDiffByFile } from '../lib/diff'
import { useDocumentBytes } from '../lib/documentBytes'
import { DOCUMENT_TYPES, IMAGE_SUFFIXES, needsDocumentView, suffixOf } from '../lib/fileKind'

import { useRawImage } from './useRawImage'

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
  /** 这一份读自频道自己的文件，不是项目当前版本。 */
  const inRoom = ref(false)

  // 频道那一份的字节端点只把 png/jpg/gif/webp 当图片发（其余只给下载），所以读自频道
  // 的 .bmp/.ico/.avif 不画成图：画出来是一张裂图，当二进制给「下载原文件」才拿得到。
  const isImage = computed(() => (inRoom.value ? IMAGE_SUFFIXES : IMAGE_EXT).has(suffixOf(opts.path())))
  const isDocument = computed(() => needsDocumentView(opts.path()))
  const documentType = computed(() => DOCUMENT_TYPES[suffixOf(opts.path())] ?? null)
  // 读自频道的那一份，图片和「下载原文件」也要从频道取：项目里没有它。
  const rawUrl = computed(() => {
    const pid = opts.projectId()
    const channel = opts.channelId()
    if (inRoom.value && channel) return attachmentRawUrl(channel, opts.path())
    return pid ? workspaceFileRawUrl(pid, opts.path(), channel ?? undefined, null, 'committed') : ''
  })
  // 读完、知道是项目的还是频道的那一份之后才取：`rawUrl` 要等 `inRoom` 定下来。
  const image = useRawImage(() =>
    isImage.value && !loading.value && !missing.value && !error.value ? rawUrl.value : ''
  )
  const doc = useDocumentBytes({
    topicId: () => opts.channelId(),
    path: () => opts.path(),
    version: () => version.value,
    task: () => null,
    source: () => 'committed',
    enabled: () => isDocument.value && !missing.value && !loading.value && !inRoom.value,
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
    inRoom.value = false
    tasks.value = []
    try {
      show(await readFile(pid, path, opts.channelId(), null, 'committed'), mine)
    } catch (e) {
      if (mine !== request) return
      if (await fromRoom(path, mine)) return
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

  function show(file: FileContent, mine: number) {
    if (mine !== request) return
    content.value = file.content ?? ''
    version.value = file.version
    bytes.value = file.bytes
    binary.value = file.binary
    tooLarge.value = file.too_large
  }

  /** 频道自己的文件里有这一份就开它，返回开没开成。 */
  async function fromRoom(path: string, mine: number): Promise<boolean> {
    const channel = opts.channelId()
    if (!channel) return false
    try {
      const file = await readPreviewFile(channel, path)
      if (mine !== request) return true
      show(file, mine)
      inRoom.value = true
      return true
    } catch {
      return false
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
    inRoom,
    isImage,
    isDocument,
    documentType,
    rawUrl,
    imageSrc: image.src,
    imageError: image.error,
    docBytes: doc.bytes,
    docLoading: doc.loading,
    docError: doc.error,
    docRendererMissing: doc.rendererMissing,
    download,
  }
}
