// Files upload into the topic's worktree; sending a message references {path, mime}.
import type { ChatAttachment } from '../cx_types'

import { ref } from 'vue'

import { uploadAttachment } from '../api'

const MAX_PENDING = 9

/** One entry in the composer's strip. While a file is still going up it is
 *  ALREADY here, holding its place, with `uploading` set — the strip renders a
 *  frame per entry, so a placeholder is what gives the spinner something to sit
 *  in. Before this, `pending` only ever held finished uploads and the strip had
 *  nothing to draw during the upload but one bare spinner, which could not say
 *  which file it belonged to or how many were in flight.
 *
 *  `path` on a placeholder is a local key, not a worktree path, so it must
 *  never be sent — `readyToSend` is the filter for that. */
export interface PendingAttachment extends ChatAttachment {
  uploading?: boolean
  /** Placeholders have no worktree path to take a name from. */
  name?: string
}

/** The entries that really exist in the worktree. Every way an attachment
 *  leaves the strip goes through this — sending it, and stashing the draft when
 *  the topic changes: a placeholder written to the draft comes back as a frame
 *  that spins for ever, because the upload it was waiting for finished in a
 *  session that is gone. */
export function uploaded(items: readonly PendingAttachment[]): ChatAttachment[] {
  return items.filter((a) => !a.uploading).map(({ path, mime }) => ({ path, mime }))
}

let placeholderSeq = 0

export function usePendingAttachments(
  getTopicId: () => string | null | undefined,
  onError?: (message: string) => void
) {
  const pending = ref<PendingAttachment[]>([])
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
        // Take the slot BEFORE the upload, so the strip has a frame to draw
        // for the whole wait instead of appearing only once it is over.
        const slot: PendingAttachment = {
          path: `uploading:${++placeholderSeq}:${f.name}`,
          mime: f.type || 'application/octet-stream',
          uploading: true,
          name: f.name,
        }
        pending.value.push(slot)
        const drop = () => {
          const at = pending.value.indexOf(slot)
          if (at >= 0) pending.value.splice(at, 1)
        }
        let attachment
        try {
          attachment = await uploadAttachment(topicId, f)
        } catch (e) {
          // A slot left behind would spin for ever.
          drop()
          throw e
        }
        if (getTopicId() !== topicId) {
          drop()
          return
        }
        // Gone from the strip means the person removed it while it was going
        // up; the finished upload is not put back.
        const at = pending.value.indexOf(slot)
        if (at >= 0) pending.value.splice(at, 1, attachment)
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
