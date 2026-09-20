// 上传的文件进项目的资料库，消息里引用的是它自己的地址 {path, mime}，不是拷贝。
import type { ChatAttachment } from '../cx_types'

import { ref } from 'vue'

import { attachLibraryFile, uploadAttachment } from '../api'

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

  async function addFiles(files: Iterable<File>, origin: 'file' | 'clipboard' = 'file') {
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
          attachment = await uploadAttachment(topicId, f, origin)
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

  /** 资料库里已经有的一份文件：不重新上传，取这个项目里那一份。 */
  async function addLibraryFile(libraryPath: string) {
    const topicId = getTopicId()
    if (!topicId) return
    if (pending.value.length >= MAX_PENDING) {
      onError?.('每条消息最多添加 9 个附件')
      return
    }
    const name = libraryPath.split('/').pop() || libraryPath
    // 同一个占位逻辑：这一步要等后端确认那份资料还在、有多大，所以它也有等待时间。
    const slot: PendingAttachment = {
      path: `uploading:${++placeholderSeq}:${name}`,
      mime: 'application/octet-stream',
      uploading: true,
      name,
    }
    pending.value.push(slot)
    const drop = () => {
      const at = pending.value.indexOf(slot)
      if (at >= 0) pending.value.splice(at, 1)
    }
    let attachment
    try {
      attachment = await attachLibraryFile(topicId, libraryPath)
    } catch (e) {
      drop()
      onError?.(e instanceof Error ? e.message : '添加文件失败')
      return
    }
    if (getTopicId() !== topicId) {
      drop()
      return
    }
    const at = pending.value.indexOf(slot)
    if (at >= 0) pending.value.splice(at, 1, attachment)
  }

  // Composer paste handler: pasted image data (e.g. a screenshot) uploads
  // instead of landing as garbled text; plain-text pastes pass through. 贴进来
  // 的那一份不进资料库——见 uploadAttachment。
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
      void addFiles(files, 'clipboard')
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

  return { pending, uploading, addFiles, addLibraryFile, onPaste, onDrop, removeAt, clear }
}
