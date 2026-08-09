// 图片输入: pending-attachment state shared by the two composers (WorkspaceView
// 的跨栏输入框和 ChatPanel 的私聊输入框). Paste or pick an image → it uploads to
// the topic's worktree immediately → the send only references {path, mime}.
import type { ChatAttachment } from '../cx_types'

import { ref } from 'vue'

import { uploadAttachment } from '../api'

const IMAGE_MIME = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])
const MAX_PENDING = 9

export function usePendingAttachments(
  getTopicId: () => string | null | undefined,
  onError?: (message: string) => void
) {
  const pending = ref<ChatAttachment[]>([])
  const uploading = ref(false)

  async function addFiles(files: Iterable<File>) {
    const topicId = getTopicId()
    if (!topicId) return
    const images = [...files].filter((f) => IMAGE_MIME.has(f.type))
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
        if (f && IMAGE_MIME.has(f.type)) files.push(f)
      }
    }
    if (files.length) {
      e.preventDefault()
      void addFiles(files)
    }
  }

  function removeAt(i: number) {
    pending.value.splice(i, 1)
  }

  function clear() {
    pending.value = []
  }

  return { pending, uploading, addFiles, onPaste, removeAt, clear }
}
