// 上传的文件进项目的资料库，消息里引用的是它自己的地址 {path, mime}，不是拷贝。
import type { ChatAttachment } from '../cx_types'

import { ref } from 'vue'

import { attachLibraryFile, uploadAttachment } from '../api'
import { t } from '../i18n'

const MAX_PENDING = 9

/** One entry in the composer's strip. While a file is still going up it is
 *  ALREADY here, holding its place, with `uploading` set — the strip renders a
 *  frame per entry, so a placeholder is what gives the spinner something to sit
 *  in. Before this, `pending` only ever held finished uploads and the strip had
 *  nothing to draw during the upload but one bare spinner, which could not say
 *  which file it belonged to or how many were in flight.
 *
 *  A failed upload keeps its slot too, with `error` set and `file` still in
 *  hand: the strip is where the failure lives, and 重试 re-runs the same bytes
 *  from there. Dropping the slot sent the reason to a toast that is gone by the
 *  time you look back at the box, and it threw the File away with it — so the
 *  one thing you wanted to do (try again) was impossible.
 *
 *  `path` on a placeholder is a local key, not a worktree path, so it must
 *  never be sent — `uploaded` is the filter for that. */
export interface PendingAttachment extends ChatAttachment {
  uploading?: boolean
  /** Upload so far, 0…1. Set from XHR's upload-progress events; absent until the
   *  first one lands (and for a worktree copy, which never had a body to send).
   *  The strip draws a determinate ring from this instead of a spinner that
   *  cannot say whether a 9 MB file is 5% or 95% of the way up. */
  progress?: number
  /** 上传失败: the slot stays with its name and File until 重试 or remove. */
  error?: boolean
  /** Placeholders have no worktree path to take a name from. */
  name?: string
  /** The picked File, kept on a failed slot so 重试 uploads the same bytes. */
  file?: File
  /** Where it came from, so a retried clipboard paste stays a clipboard paste. */
  origin?: 'file' | 'clipboard'
}

/** What `File.type` alone cannot give back: the extension to write on a name we
 *  are inventing for a pasted image. */
const IMAGE_EXT: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/gif': 'gif',
  'image/webp': 'webp',
  'image/bmp': 'bmp',
  'image/svg+xml': 'svg',
}

/** The names a browser makes up for a clipboard image. `image.png` is Chromium's
 *  guess on every platform; `blob` is what a canvas/toBlob paste carries. A real
 *  name (a screenshot tool's, a file manager's) is none of these and is kept. */
const GENERIC_PASTED = /^(image|blob|clipboard)(\.(png|jpe?g|gif|webp|bmp|svg))?$/i

/** `yyyyMMdd-HHmmss`, local time: sorts, and is a legal filename everywhere. */
function stamp(at: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${at.getFullYear()}${p(at.getMonth() + 1)}${p(at.getDate())}-${p(at.getHours())}${p(at.getMinutes())}${p(at.getSeconds())}`
}

/** A screenshot pasted from the clipboard arrives called `image.png`, so the
 *  strip and the room's copy of it carry nothing to tell two pastes apart (and a
 *  name that only means "a picture"). Give it one that says when it was taken. */
export function pastedImageName(file: File, at: Date = new Date()): string {
  const ext = IMAGE_EXT[file.type] ?? file.name.split('.').pop() ?? 'png'
  return `${t('work.room.attachments.pastedName', { time: stamp(at) })}.${ext}`
}

/** The name for a long plain-text paste turned into a file. Same shape as a
 *  pasted image: what it is, and when. */
export function pastedTextName(ext: 'txt' | 'md', at: Date = new Date()): string {
  return `${t('work.room.attachments.pastedTextName', { time: stamp(at) })}.${ext}`
}

/** Whether a pasted File needs an invented name: an image the browser named. */
function needsPastedName(file: File): boolean {
  return file.type.startsWith('image/') && (!file.name || GENERIC_PASTED.test(file.name))
}

/** A copy of `file` under a different name — the bytes and type are untouched. */
function renamed(file: File, name: string): File {
  return new File([file], name, { type: file.type, lastModified: file.lastModified })
}

/** The entries that really exist in the worktree. Every way an attachment
 *  leaves the strip goes through this — sending it, and stashing the draft when
 *  the topic changes: a placeholder written to the draft comes back as a frame
 *  that spins for ever, because the upload it was waiting for finished in a
 *  session that is gone. A failed slot is not in the worktree either, so it is
 *  left out for the same reason. */
export function uploaded(items: readonly PendingAttachment[]): ChatAttachment[] {
  return items.filter((a) => !a.uploading && !a.error).map(({ path, mime }) => ({ path, mime }))
}

let placeholderSeq = 0

export function usePendingAttachments(
  getTopicId: () => string | null | undefined,
  onError?: (message: string) => void
) {
  const pending = ref<PendingAttachment[]>([])
  const uploading = ref(false)

  function drop(slot: PendingAttachment) {
    const at = pending.value.indexOf(slot)
    if (at >= 0) pending.value.splice(at, 1)
  }

  /** Put this file where the slot is, or mark that slot failed and keep it.
   *  Used both on the first try and on 重试: one upload, one place it lands. */
  async function uploadInto(slot: PendingAttachment, file: File, origin: 'file' | 'clipboard') {
    const topicId = getTopicId()
    if (!topicId) {
      drop(slot)
      return
    }
    slot.error = false
    slot.uploading = true
    slot.progress = 0
    let attachment: ChatAttachment
    try {
      attachment = await uploadAttachment(topicId, file, origin, (fraction) => {
        // Only while the slot is still on its way: once it lands it is replaced
        // by the finished attachment, and a late event must not resurrect it.
        if (slot.uploading) slot.progress = fraction
      })
    } catch {
      // The slot keeps its place: 重试 repeats this same upload, and until it
      // does the strip itself says why this file is not going anywhere.
      if (pending.value.includes(slot)) {
        slot.error = true
        slot.uploading = false
      }
      return
    }
    if (getTopicId() !== topicId) {
      drop(slot)
      return
    }
    // Gone from the strip means the person removed it while it was going up; the
    // finished upload is not put back.
    const at = pending.value.indexOf(slot)
    if (at >= 0) pending.value.splice(at, 1, attachment)
  }

  async function addFiles(files: Iterable<File>, origin: 'file' | 'clipboard' = 'file') {
    const topicId = getTopicId()
    if (!topicId) return
    const all = [...files]
    if (!all.length) return
    uploading.value = true
    try {
      for (const f of all) {
        if (pending.value.length >= MAX_PENDING) {
          onError?.(t('work.room.attachments.tooMany'))
          break
        }
        if (f.size > 10 * 1024 * 1024) {
          onError?.(t('work.room.attachments.tooLarge', { name: f.name }))
          continue
        }
        // Take the slot BEFORE the upload, so the strip has a frame to draw
        // for the whole wait instead of appearing only once it is over.
        const slot: PendingAttachment = {
          path: `uploading:${++placeholderSeq}:${f.name}`,
          mime: f.type || 'application/octet-stream',
          uploading: true,
          name: f.name,
          file: f,
          origin,
        }
        pending.value.push(slot)
        await uploadInto(slot, f, origin)
      }
    } finally {
      uploading.value = false
    }
  }

  /** 重试 one failed upload: the same slot, the same bytes — no second frame. */
  async function retry(i: number) {
    const slot = pending.value[i]
    if (!slot || slot.uploading || !slot.error || !slot.file) return
    uploading.value = true
    try {
      await uploadInto(slot, slot.file, slot.origin ?? 'file')
    } finally {
      uploading.value = false
    }
  }

  /** 资料库里已经有的一份文件：不重新上传，取这个项目里那一份。 */
  async function addLibraryFile(libraryPath: string) {
    const topicId = getTopicId()
    if (!topicId) return
    if (pending.value.length >= MAX_PENDING) {
      onError?.(t('work.room.attachments.tooMany'))
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
    const dropSlot = () => {
      const at = pending.value.indexOf(slot)
      if (at >= 0) pending.value.splice(at, 1)
    }
    let attachment
    try {
      attachment = await attachLibraryFile(topicId, libraryPath)
    } catch (e) {
      dropSlot()
      onError?.(e instanceof Error ? e.message : t('work.room.attachments.addFailed'))
      return
    }
    if (getTopicId() !== topicId) {
      dropSlot()
      return
    }
    const at = pending.value.indexOf(slot)
    if (at >= 0) pending.value.splice(at, 1, attachment)
  }

  // Composer paste handler: pasted image data (e.g. a screenshot) uploads
  // instead of landing as garbled text; plain-text pastes pass through. 贴进来
  // 的那一份不进资料库——见 uploadAttachment。浏览器给截图起的名（`image.png`）
  // 换成带时间的名字，两次粘贴才分得开。
  function onPaste(e: ClipboardEvent) {
    const items = e.clipboardData?.items
    if (!items) return
    const files: File[] = []
    for (const item of Array.from(items)) {
      if (item.kind === 'file') {
        const f = item.getAsFile()
        if (f) files.push(needsPastedName(f) ? renamed(f, pastedImageName(f)) : f)
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

  return { pending, uploading, addFiles, addLibraryFile, onPaste, onDrop, removeAt, retry, clear }
}
