// 总览页上点了「让 AI 队友起草」：起草发生在项目总览那篇文档里（文档里的 AI 队友写，
// 写的过程和别的修改一样在正文里看得到），所以总览页只把要起草哪一篇、补充了什么留
// 在这里，跳到文档页；那篇文档一打开、能问 AI 队友了，就由它接手。
import { shallowRef } from 'vue'

interface PendingDraft {
  documentId: string
  /** 提问人的补充说明，连同放进资料库的材料的地址。 */
  note: string
}

const pending = shallowRef<PendingDraft | null>(null)

export function requestDraft(documentId: string, note: string): void {
  pending.value = { documentId, note }
}

/** 这一篇有没有在等起草。 */
export function draftWaiting(documentId: string | null | undefined): boolean {
  return !!documentId && pending.value?.documentId === documentId
}

/** 接手这一篇的起草：给回补充说明，并把它从等着的里拿掉；没在等就是 null。 */
export function takeDraft(documentId: string): string | null {
  const found = pending.value
  if (!found || found.documentId !== documentId) return null
  pending.value = null
  return found.note
}
