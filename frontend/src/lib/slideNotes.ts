// 演示稿每一页的演讲者备注，和「这一页是不是隐藏页」。
//
// 服务端从原稿里读（`backend/app/domain/documents/notes.py`），一页一项、按放映顺序、隐藏页
// 也在。预览画的是转换后的 PDF，所以两边要对上页：这一层只管「第几页的 PDF 对应第几项」。

export type SlideNoteRun = { text: string; bold?: boolean; italic?: boolean }
export type SlideNoteParagraph = {
  runs: readonly SlideNoteRun[]
  /** PowerPoint 的大纲级别，0 是最外层。 */
  level?: number
  bullet?: 'dot' | 'number' | null
}
export type SlideNote = {
  hidden: boolean
  paragraphs: readonly SlideNoteParagraph[]
}

/** 接口回来的那一包：`slides` 是带格式的；老后端只给 `notes`（纯文本、一页一串）。 */
export type SlideNotesPayload = {
  notes: readonly string[]
  slides?: readonly SlideNote[]
}

/** 纯文本的一页备注拆成段落：老后端只给这个，换行就是分段。 */
function plain(text: string): SlideNote {
  return {
    hidden: false,
    paragraphs: text
      .split('\n')
      .filter((line) => line.trim())
      .map((line) => ({ runs: [{ text: line }] })),
  }
}

export function slideNotesOf(payload: SlideNotesPayload): SlideNote[] {
  if (payload.slides?.length === payload.notes.length && payload.slides.length) return [...payload.slides]
  return payload.notes.map(plain)
}

export function noteText(note: SlideNote | undefined): string {
  return (note?.paragraphs ?? []).map((p) => p.runs.map((r) => r.text).join('')).join('\n')
}

export function hasNoteText(note: SlideNote | undefined): boolean {
  return !!noteText(note).trim()
}

/** 第 n 页的 PDF 对应哪一项备注（下标即 PDF 页码减一）。
 *
 *  正常情况一一对应：转换时隐藏页也导出了。有两种对不上：
 *  - 转换服务还是老版本（或缓存里还是老 PDF），隐藏页没导出 → PDF 页数等于「不隐藏的页数」，
 *    就只拿不隐藏的那几项来对，这时屏上也没有隐藏页可标。
 *  - 别的原因页数对不上 → 宁可不给：把别的页的讲稿贴到这一页上，比没有更坏。 */
export function alignSlideNotes(notes: readonly SlideNote[], pages: number): SlideNote[] {
  if (!pages || !notes.length) return []
  if (notes.length === pages) return [...notes]
  const shown = notes.filter((note) => !note.hidden)
  if (shown.length === pages) return shown
  return []
}

/** 备注那一条最矮多高（像素）：再矮连一行字都放不下。 */
export const NOTES_MIN_PX = 72
