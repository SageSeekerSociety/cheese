// 「指哪里、说哪句话」这一套：读者指着文档里的一处、表里的一格、幻灯片的一页、图上
// 一块，或者网页里选中的一段，提一句话交给房间的对话。
//
// 四种指法各有自己的判据（图上那一块要的是显示着的字节，页上一处要的是已核过的上下文，
// 网页里那一处来自帧或遮罩），但收口只有一条：定位条上写一句话，`sendLocator` 把它和
// 位置拼成一条消息发出去。指的时候只取「文件、位置、原文」三样，不做能长期保留的批注
// ——读者要改的那句话正是芝士下一轮要改掉的那句话，锚点必然失效（理由见
// `markdownQuote.ts` 开头）。
//
// 换了一份文件就把上一处的指认撤掉：那句话说的是文件 A 里的位置，发出去时标题上写的
// 却是文件 B 的名字（下面的 `watch`）。
import type { AnnotateDraft } from '@/composables/usePanelPreview'
import type { ChatAttachment } from '@/cx_types'
import type { PreviewLocate } from '@/lib/previewQuestion'
import type { RasterSelection } from './designRegion'
import type { MarkdownQuote, QuoteContext } from './markdownQuote'
import type { PreviewViewProps } from './previewViewProps'
import type { SlidePageContext } from './slidesContext'

import { ref, watch } from 'vue'

import { usePreviewImageRegion } from './usePreviewImageRegion'
import { usePreviewPagePin } from './usePreviewPagePin'
import { usePreviewPick } from './usePreviewPick'
import { usePreviewQuote } from './usePreviewQuote'

import { t } from '@/i18n'
import { firstCodePoints } from '@/lib/codePoints'

interface LocatorDeps {
  /** 指了一处，交给房间的对话。 */
  onLocate: (payload: PreviewLocate) => void
  /** 圈选开关变了：有桥的网页要把它递进帧。 */
  onPickMode: (on: boolean) => void
  /** 把文档某一页此刻的样子截下来（`PreviewPages` 的实例方法），钉一处时随行带上。 */
  snapshot: (page: number) => Promise<{ blob: Blob } | null>
  /** 撤掉页上那个钉：换了文件、重取之后它指向的位置不成立了。 */
  clearMark: () => void
}

export function usePreviewLocator(props: PreviewViewProps, deps: LocatorDeps) {
  const locator = ref<{ label: string; quote: string; address: string; context?: QuoteContext } | null>(null)
  const locatorNote = ref('')
  const queued = ref<string[]>([])
  /** 标注图传不上去时的那一句：不说的话，画完按了按钮看起来像什么都没发生。 */
  const annotateError = ref('')

  const imageRegion = usePreviewImageRegion(props, clearLocator)
  const pageLocator = usePreviewPagePin(props, {
    snapshot: async (page) => (await deps.snapshot(page)) ?? null,
    open: openLocator,
    clear: clearLocator,
  })
  const quoted = usePreviewQuote(props, (context) => pageLocator.canUse(context))

  // 网页预览里的圈选（判据和开关在 usePreviewPick）：有桥的网页由帧报回一处，应用由遮罩报回一块。
  const pick = usePreviewPick({
    frame: () => props.displayedFrame,
    previewUrl: () => props.previewUrl,
    onRuntime: deps.onPickMode,
    open: (label, quote, address) => openLocator(label, quote, address),
    quote: quoted,
  })

  function openLocator(label: string, quote: string, address: string, context?: QuoteContext) {
    imageRegion.clear()
    quoted.clear()
    pageLocator.pin.value = null
    locator.value = { label, quote, address, context }
    locatorNote.value = ''
  }

  function clearLocator() {
    imageRegion.clear()
    locator.value = null
    quoted.clear()
    pageLocator.pin.value = null
    deps.clearMark()
    locatorNote.value = ''
  }

  function onImageRegion(selection: RasterSelection) {
    const captured = imageRegion.capture(selection)
    if (!captured) return
    openLocator(t('design.region'), t('design.selectedRegion', selection.region), '')
    imageRegion.target.value = captured
    // 框选的位置同时作为一处结构化引用发出去：合成图说得出「图上哪一块」，这句话说得出
    // 「要改成什么」；只有书面那句话的受话人回到的是「图片这一份」，回不到那一块。坐标
    // 用的是原图像素（选区就是这么量的），发的时候才配上这一版的身份。
    quoted.imageRegion({
      ...selection.region,
      naturalWidth: selection.naturalWidth,
      naturalHeight: selection.naturalHeight,
    })
  }

  function onPageContext(payload: SlidePageContext) {
    if (!pageLocator.canUse(payload.context)) return
    const page = t('work.room.preview.page', { page: payload.page })
    openLocator(page, payload.scope === 'page' ? t('slides.wholePage') : firstCodePoints(payload.text, 200), page)
    quoted.page.value = { ...payload, context: { ...payload.context } }
  }

  watch(
    [
      // markdown 没有下面那套文档身份，屏幕上换了一份文件时没人撤掉上一份的指认——
      // 那句话说的是文件 A 里的位置，发出去时标题上写的却是文件 B 的名字。
      () => props.previewFile?.path,
      () => props.docBytes,
      () => props.docSnapshot,
      () => props.docIdentity?.topicId,
      () => props.docIdentity?.path,
      () => props.docIdentity?.source,
      () => props.docIdentity?.taskId,
      () => props.docIdentity?.version,
      () => props.slideContext?.version,
      () => props.slideContext?.path,
      () => props.slideContext?.source,
      () => props.slideContext?.topicId,
      () => props.slideContext?.taskId,
    ],
    () => {
      clearLocator()
      queued.value = []
    },
    { flush: 'sync' }
  )

  function onQuote(payload: { text: string; page: number }) {
    // 一整页的选中没有指向性，当作没指。
    const quote = payload.text.replace(/\s+/g, ' ').trim()
    if (quote.length < 2) return
    const page = t('work.room.preview.page', { page: payload.page })
    openLocator(page, firstCodePoints(quote, 200), page)
  }

  function onCell(payload: { address: string; value: string; sheet: string }) {
    // CSV 没有工作表名，`!B7` 会让读者以为前面漏了个名字。
    const where = payload.sheet ? `${payload.sheet}!${payload.address}` : payload.address
    openLocator(where, payload.value || t('work.room.preview.emptyCell'), where)
    quoted.cell(payload)
  }

  /** 表格里拖过的一片：位置写成表格软件自己印的那个 `A1:C3`。 */
  function onSheetRange(payload: { sheet: string; start: string; end: string; text: string }) {
    const area = `${payload.start}:${payload.end}`
    const where = payload.sheet ? `${payload.sheet}!${area}` : area
    // 一片全是空格子时摊平的结果是几个制表符和换行，看着不是空的却什么也没有：按空白判，
    // 和 `MessageQuote` 那条同一条。判的是整片的内容，不是截出来那 200 字——开头一片空格、
    // 右下角有内容的表不能被说成空的。截预览按码点，和表格那边怎么截同一条。
    const preview = firstCodePoints(payload.text, 200)
    openLocator(where, payload.text.trim() ? preview : t('work.room.preview.emptyCell'), where)
    quoted.sheetRange(payload)
  }

  function onMarkdownQuote(payload: MarkdownQuote) {
    // 位置说的是「哪一节」而不是行号：改一句话要重新渲染，行号下一版就不成立了；它之前没有标题就是文件开头。
    const where = payload.heading
      ? t('work.room.preview.mdHeading', { heading: payload.heading })
      : t('work.room.preview.mdTop')
    openLocator(where, firstCodePoints(payload.text, 200), where, {
      prefix: payload.prefix,
      suffix: payload.suffix,
    })
    quoted.range(payload)
  }

  function contextLine({ prefix, suffix }: QuoteContext): string {
    if (prefix && suffix) return t('work.room.preview.locateContext', { prefix, suffix })
    // 空的那一侧不写——写出来只是一对空引号。
    if (prefix) return t('work.room.preview.locateContextBefore', { prefix })
    return t('work.room.preview.locateContextAfter', { suffix })
  }

  function formatLocateMessage(
    target: { label: string; quote: string; address: string; context?: QuoteContext },
    note: string
  ): string {
    const message = t('work.room.preview.locateMessage', {
      path: props.previewFile?.path ?? '',
      address: target.address || target.label,
      quote: target.quote,
      note,
    })
    const ctx = target.context
    const context = ctx && (ctx.prefix || ctx.suffix) ? contextLine(ctx) : ''
    return context ? `${message}\n${context}` : message
  }

  function queueLocator() {
    const target = locator.value
    const note = locatorNote.value.trim()
    if (!target || !note || pageLocator.pin.value) return
    queued.value = [...queued.value, formatLocateMessage(target, note)]
    clearLocator()
  }

  function clearQueued() {
    queued.value = []
  }

  function sendQueued() {
    const target = locator.value
    const note = locatorNote.value.trim()
    const pending = [...queued.value]
    if (target && note && !pageLocator.pin.value) {
      pending.push(formatLocateMessage(target, note))
    }
    if (!pending.length) return
    deps.onLocate({ message: pending.join('\n\n') })
    queued.value = []
    if (!pageLocator.pin.value) clearLocator()
  }

  function sendLocator() {
    const target = locator.value
    const note = locatorNote.value.trim()
    if (!target || !note) return
    if (pageLocator.pin.value) {
      void pageLocator.send(note)
      return
    }
    if (queued.value.length > 0) {
      sendQueued()
      return
    }
    if (imageRegion.target.value) {
      // 框出来的那一块走结构化引用；发不出去（这份部署没有提问出口、或拿不到身份）才
      // 退回拼一句话——那句话指得回「图片这一份」，指不回那一块，不能白丢。
      const sent = quoted.send(note)
      if (sent !== null) {
        if (sent) clearLocator()
        return
      }
      const message = imageRegion.message(note)
      if (!message) return
      deps.onLocate({ message })
      clearLocator()
      return
    }
    // 结构化引用发得出去就走它；没有可发的（null）才退回下面拼一句话那条老路。
    const sent = quoted.send(note)
    if (sent !== null) {
      if (sent) clearLocator()
      return
    }
    deps.onLocate({ message: formatLocateMessage(target, note) })
    clearLocator()
  }

  /** 图上画完、按了「加入对话」：把那张合成图交出去传进房间，再发那一句连同附件。
   *
   * 上传是外面递进来的能力（`uploadAnnotation`），消息要等它回来才拼得出来。发之前再
   * 核一次版本：合成的是屏幕上那张图，版本在画的过程中被人换掉时宁可不发，也不能配着
   * 一张说的不是它的图发出去。 */
  async function onAnnotate(payload: AnnotateDraft) {
    const topicId = props.topicId
    const identity = props.docIdentity
    const upload = props.uploadAnnotation
    if (!topicId || !identity || !upload || !imageRegion.selectionEnabled.value) return
    let attachment: ChatAttachment
    try {
      attachment = await upload(topicId, payload)
    } catch (error) {
      annotateError.value = error instanceof Error ? error.message : String(error)
      return
    }
    if (props.topicId !== topicId) return
    annotateError.value = ''
    deps.onLocate({
      message: t('design.sketchMessage', {
        ...identity,
        task: identity.taskId ?? '',
        naturalWidth: payload.naturalWidth,
        naturalHeight: payload.naturalHeight,
        count: payload.count,
        note: payload.note,
      }),
      attachments: [attachment],
    })
  }

  return {
    locator,
    locatorNote,
    queued,
    annotateError,
    imageRegion,
    pageLocator,
    quoted,
    pick,
    openLocator,
    clearLocator,
    queueLocator,
    clearQueued,
    sendQueued,
    sendLocator,
    onImageRegion,
    onPageContext,
    onQuote,
    onCell,
    onSheetRange,
    onMarkdownQuote,
    onAnnotate,
  }
}
