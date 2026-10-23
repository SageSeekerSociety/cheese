import type { Block } from '../cx_types'

/**
 * 一段对话的一章（`GET /topics/{id}/chapters`）：从哪条消息开始，谁、什么时候钉的。
 * 列表按对话里的顺序给，第几条就是它在对话里的位置。
 */
export interface ChapterRow {
  pinned_by: string
  pinned_at: string
  /** 这条书签叫什么：加的时候从消息里读的短名，人改过或模型改过就是那一版。
   *  null 表示消息里没有可取的文字，由界面说「没有字的书签」。 */
  title: string | null
  /** 这一章开始的那条消息。 */
  block: Block
}
