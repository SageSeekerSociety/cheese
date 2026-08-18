// 图片输入: pending-attachment state shared by the two composers (TopicView
// 的跨栏输入框和 ChatPanel 的私聊输入框). Paste or pick an image → it uploads to
// the topic's worktree immediately → the send only references {path, mime}.
import type { ChatAttachment } from '../cx_types'

import { ref } from 'vue'

import { uploadAttachment } from '../api'

const IMAGE_MIME = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])
const MAX_PENDING = 9
// 后端今天只收图片（`attachment_raw` 是刻意按扩展名白名单的，别的类型要配一条
// 单独的下载通道才安全）。这个上限本身不是 bug——**不出声地把文件扔掉**才是。
const UNSUPPORTED = '暂时只能发图片（PNG / JPEG / GIF / WebP）'

export function usePendingAttachments(
  getTopicId: () => string | null | undefined,
  onError?: (message: string) => void
) {
  const pending = ref<ChatAttachment[]>([])
  const uploading = ref(false)

  async function addFiles(files: Iterable<File>) {
    const topicId = getTopicId()
    if (!topicId) return
    const all = [...files]
    const images = all.filter((f) => IMAGE_MIME.has(f.type))
    // 拖一个 PDF 进来，过去是：没上传、没报错、没有任何提示。文件就是消失了。
    if (images.length < all.length) onError?.(UNSUPPORTED)
    if (!images.length) return
    uploading.value = true
    try {
      for (const f of images) {
        if (pending.value.length >= MAX_PENDING) break
        pending.value.push(await uploadAttachment(topicId, f))
      }
    } catch (e) {
      onError?.(e instanceof Error ? e.message : '图片上传失败')
    } finally {
      uploading.value = false
    }
  }

  // Composer paste handler: pasted image data (e.g. a screenshot) uploads
  // instead of landing as garbled text; plain-text pastes pass through.
  function onPaste(e: ClipboardEvent) {
    const items = e.clipboardData?.items
    if (!items) return
    const files: File[] = []
    for (const item of Array.from(items)) {
      if (item.kind === 'file') {
        const f = item.getAsFile()
        if (f) files.push(f)
      }
    }
    if (files.length) {
      e.preventDefault()
      void addFiles(files)
    }
  }

  /** 拖进来的文件 (spec §7.1「拖到输入栏」)。 */
  function onDrop(e: DragEvent) {
    const files = e.dataTransfer?.files
    if (!files?.length) return
    e.preventDefault()
    void addFiles(Array.from(files))
  }

  function removeAt(i: number) {
    pending.value.splice(i, 1)
  }

  function clear() {
    pending.value = []
  }

  return { pending, uploading, addFiles, onPaste, onDrop, removeAt, clear }
}
