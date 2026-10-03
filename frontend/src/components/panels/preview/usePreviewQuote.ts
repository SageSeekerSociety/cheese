import type { DocumentIdentity } from '@/lib/documentBytes'
import type { SubmitPreviewQuestion } from '@/lib/previewQuestion'
import type { SheetCellQuote, TextRangeQuote } from '@/lib/quotedContext'
import type { MarkdownQuote } from './markdownQuote'
import type { SlidePageContext } from './slidesContext'

import { ref } from 'vue'

interface QuoteProps {
  submitQuestion?: SubmitPreviewQuestion
  docIdentity?: DocumentIdentity | null
}

type Identity = 'path' | 'source' | 'version' | 'task_id'
/** 表格里的一格、渲染正文里的一段：指的那一刻留下来，发的时候才配上文件身份。 */
type FilePick = Omit<SheetCellQuote, Identity> | Omit<TextRangeQuote, Identity>

/** 指着文件里一处提问时，随消息走的那份结构化引用。
 *
 *  幻灯片的一页带着自己那份已核过字节的上下文（`canUse` 再核一次）；表格的一格和
 *  markdown 的一段用面板上的文件身份。拿不到身份（缺版本）或这份部署没有提问出口时
 *  `send` 答 `null`，调用方退回拼一句话那条老路，不硬造引用。 */
export function usePreviewQuote(props: QuoteProps, canUse: (context: SlidePageContext['context']) => boolean) {
  const page = ref<SlidePageContext | null>(null)
  const pick = ref<FilePick | null>(null)

  function clear() {
    page.value = null
    pick.value = null
  }

  function cell(payload: { address: string; value: string; sheet: string }) {
    pick.value = { kind: 'sheet-cell', sheet: payload.sheet, address: payload.address, value: payload.value }
  }

  function range(payload: MarkdownQuote) {
    // 文件开头那一段没有标题；发出去写 null，别让空串冒充一个叫「」的标题。
    const { text, heading, prefix, suffix } = payload
    pick.value = { kind: 'text-range', text, heading: heading || null, prefix, suffix }
  }

  /** 发出去了答 true，被拒了答 false，没有可发的结构化引用答 null。 */
  function send(note: string): boolean | null {
    if (page.value) {
      const payload = page.value
      if (!canUse(payload.context)) return false
      const { path, source, version, taskId, topicId } = payload.context
      return !!props.submitQuestion?.({
        intent: 'ask-agent',
        topicId,
        content: note,
        quotedContext: {
          kind: 'slide-page',
          path,
          source,
          version,
          task_id: taskId ?? null,
          page: payload.page,
          scope: payload.scope,
          text: payload.text,
        },
      })
    }
    const identity = props.docIdentity
    if (!pick.value || !identity?.version || !props.submitQuestion) return null
    const { path, source, version, taskId, topicId } = identity
    return !!props.submitQuestion({
      intent: 'ask-agent',
      topicId,
      content: note,
      quotedContext: { ...pick.value, path, source, version, task_id: taskId ?? null },
    })
  }

  return { page, pick, clear, cell, range, send }
}
