import type { FramePick } from '@/composables/usePreviewFrames'
import type { DocumentIdentity } from '@/lib/documentIdentity'
import type { SubmitPreviewQuestion } from '@/lib/previewQuestion'
import type {
  ImageRegionQuote,
  QuotedContext,
  SheetCellQuote,
  SheetRangeQuote,
  TextRangeQuote,
  WebElementQuote,
  WebRect,
  WebTextQuote,
  WebViewport,
} from '@/lib/quotedContext'
import type { MarkdownQuote } from './markdownQuote'
import type { SlidePageContext } from './slidesContext'

import { ref } from 'vue'

import { isQuotedContext } from '@/lib/quotedContext'

interface QuoteProps {
  submitQuestion?: SubmitPreviewQuestion
  docIdentity?: DocumentIdentity | null
  topicId?: string | null
}

type Identity = 'path' | 'source' | 'version' | 'task_id'
/** 表格里的一格、渲染正文里的一段、网页里点中的元素 / 选中的一段：指的那一刻留下来，
 *  发的时候才配上文件身份。 */
type FilePick =
  | Omit<SheetCellQuote, Identity>
  | Omit<SheetRangeQuote, Identity>
  | Omit<TextRangeQuote, Identity>
  | Omit<WebElementQuote, Identity>
  | Omit<WebTextQuote, Identity>

/** 指着文件里一处提问时，随消息走的那份结构化引用。
 *
 *  幻灯片的一页带着自己那份已核过字节的上下文（`canUse` 再核一次）；表格的一格和
 *  markdown 的一段用面板上的文件身份。拿不到身份（缺版本）或这份部署没有提问出口时
 *  `send` 答 `null`，调用方退回拼一句话那条老路，不硬造引用。 */
export function usePreviewQuote(props: QuoteProps, canUse: (context: SlidePageContext['context']) => boolean) {
  const page = ref<SlidePageContext | null>(null)
  const pick = ref<FilePick | null>(null)
  /** 应用上圈的一块：页面在别的源上，只有地址和几何，没有文件身份。 */
  const area = ref<{ url: string; rect: WebRect; viewport: WebViewport } | null>(null)
  /** 图片上框的一块：原图像素加当时的原图尺寸。几何本身不带身份，发的时候才配上。 */
  const image = ref<Omit<ImageRegionQuote, Identity> | null>(null)

  function clear() {
    page.value = null
    pick.value = null
    area.value = null
    image.value = null
  }

  function region(payload: { url: string; rect: WebRect; viewport: WebViewport }) {
    area.value = payload
  }

  /** 图片上框的一块。坐标是原图像素，和显示缩放无关。 */
  function imageRegion(payload: {
    x: number
    y: number
    width: number
    height: number
    naturalWidth: number
    naturalHeight: number
  }) {
    image.value = {
      kind: 'image-region',
      x: payload.x,
      y: payload.y,
      width: payload.width,
      height: payload.height,
      natural_width: payload.naturalWidth,
      natural_height: payload.naturalHeight,
    }
  }

  function cell(payload: { address: string; value: string; sheet: string }) {
    pick.value = { kind: 'sheet-cell', sheet: payload.sheet, address: payload.address, value: payload.value }
  }

  /** 表格里拖过的一片。两个角和内容都是指的那一刻量下来的；发的时候才配文件身份。 */
  function sheetRange(payload: { sheet: string; start: string; end: string; text: string }) {
    pick.value = {
      kind: 'sheet-range',
      sheet: payload.sheet,
      start: payload.start,
      end: payload.end,
      text: payload.text,
    }
  }

  function range(payload: MarkdownQuote) {
    // 文件开头那一段没有标题；发出去写 null，别让空串冒充一个叫「」的标题。
    const { text, heading, prefix, suffix } = payload
    pick.value = { kind: 'text-range', text, heading: heading || null, prefix, suffix }
  }

  /** 网页预览里圈选的一处：点中一个元素，或者选中一段文字。位置（选择器、标签）和当时
   *  量出来的几何都在帧报上来的 `payload` 里，发的时候再配上这一版文件的身份。 */
  function web(payload: FramePick) {
    if (payload.selection) {
      pick.value = {
        kind: 'web-text',
        selector: payload.selector,
        tag: payload.tag,
        ...(payload.label ? { label: payload.label } : {}),
        text: payload.text,
        prefix: payload.prefix,
        suffix: payload.suffix,
        rect: payload.rect,
        viewport: payload.viewport,
      }
    } else {
      pick.value = {
        kind: 'web-element',
        selector: payload.selector,
        tag: payload.tag,
        ...(payload.label ? { label: payload.label } : {}),
        text: payload.text,
        rect: payload.rect,
        viewport: payload.viewport,
      }
    }
  }

  /** 发出去了答 true；幻灯片那一页已不可信答 false；该退回拼一句话时答 null。 */
  function send(note: string): boolean | null {
    if (area.value) {
      const topicId = props.topicId
      const quotedContext = { kind: 'web-region' as const, ...area.value }
      if (!topicId || !props.submitQuestion || !isQuotedContext(quotedContext)) return null
      return props.submitQuestion({ intent: 'ask-agent', topicId, content: note, quotedContext }) ? true : null
    }
    if (image.value) {
      const identity = props.docIdentity
      // 图片的那一块要连同这一版的身份才指得回去；拿不到身份就退回拼一句话那条路。
      if (!identity?.version || !props.submitQuestion) return null
      const { path, source, version, taskId, topicId } = identity
      const quotedContext = { ...image.value, path, source, version, task_id: taskId ?? null }
      if (!isQuotedContext(quotedContext)) return null
      return props.submitQuestion({ intent: 'ask-agent', topicId, content: note, quotedContext }) ? true : null
    }
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
          // 选中一段才有前后文；整页这一支是 undefined，序列化时连键都不出现。
          prefix: payload.scope === 'selection' ? payload.prefix : undefined,
          suffix: payload.scope === 'selection' ? payload.suffix : undefined,
        },
      })
    }
    const identity = props.docIdentity
    if (!pick.value || !identity?.version || !props.submitQuestion) return null
    const { path, source, version, taskId, topicId } = identity
    const quotedContext = { ...pick.value, path, source, version, task_id: taskId ?? null }
    // 先自己核一遍形状，别让后端回一个 422。
    if (!isQuotedContext(quotedContext)) return null
    // 提问出口不收（房间里没有芝士的席位）时，这一格原来是作为一句话发进房间的，
    // 那条路照旧走：答 null 而不是 false，免得人写的那句话无声无息地没了。
    return props.submitQuestion({ intent: 'ask-agent', topicId, content: note, quotedContext }) ? true : null
  }

  /** 此刻指着的这一处，按结构化引用的形状给出来（没指、或拿不到文件身份时 null）。
   *
   *  和 `send` 拼的是同一份东西，只是不发出去：留下一条评论时，锚点要的就是它。 */
  function anchor(): QuotedContext | null {
    if (area.value) {
      const quotedContext = { kind: 'web-region' as const, ...area.value }
      return isQuotedContext(quotedContext) ? quotedContext : null
    }
    // 图片上框的那一块也要给出来：评论栏已经开着时读者可以直接在里面写一条，那时
    // `commentPick` 带的是这一块的位置文字；锚点要是 null，这条新评论会被画成「这一处
    // 已经不在原处了」（`PreviewComments` 的 `placeOf` 只认带版本号的锚点）。
    // 还没做的是「在图上看得到这一处」：没有地方画 `image-region` 的标记，框着时评论
    // 按钮也收起来，所以它目前只从已经开着的那条评论栏里被写下去。
    if (image.value) {
      const identity = props.docIdentity
      if (!identity?.version) return null
      const { path, source, version, taskId } = identity
      const quotedContext = { ...image.value, path, source, version, task_id: taskId ?? null }
      return isQuotedContext(quotedContext) ? quotedContext : null
    }
    if (page.value) {
      const payload = page.value
      if (!canUse(payload.context)) return null
      const { path, source, version, taskId } = payload.context
      const quotedContext = {
        kind: 'slide-page' as const,
        path,
        source,
        version,
        task_id: taskId ?? null,
        page: payload.page,
        scope: payload.scope,
        text: payload.text,
        prefix: payload.scope === 'selection' ? payload.prefix : undefined,
        suffix: payload.scope === 'selection' ? payload.suffix : undefined,
      }
      return isQuotedContext(quotedContext) ? quotedContext : null
    }
    const identity = props.docIdentity
    if (!pick.value || !identity?.version) return null
    const { path, source, version, taskId } = identity
    const quotedContext = { ...pick.value, path, source, version, task_id: taskId ?? null }
    return isQuotedContext(quotedContext) ? quotedContext : null
  }

  return { page, pick, clear, cell, sheetRange, range, web, region, imageRegion, send, anchor }
}
