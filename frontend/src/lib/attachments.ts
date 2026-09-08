// Files upload into the topic's worktree; sending a message references {path, mime}.
import type { ChatAttachment } from '../cx_types'

import { ref } from 'vue'

import { uploadAttachment } from '../api'

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
    const all = [...files]
    if (!all.length) return
    uploading.value = true
    try {
      for (const f of all) {
        if (pending.value.length >= MAX_PENDING) {
          onError?.('每条消息最多添加 9 个附件')
          break
        }
        if (f.size > 10 * 1024 * 1024) {
          onError?.(`${f.name} 超过 10MB，无法上传`)
          continue
        }
        const attachment = await uploadAttachment(topicId, f)
        if (getTopicId() !== topicId) return
        pending.value.push(attachment)
      }
    } catch (e) {
      onError?.(e instanceof Error ? e.message : '文件上传失败')
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
