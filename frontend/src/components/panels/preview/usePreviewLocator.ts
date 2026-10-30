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
import type { PreviewModeApi } from '@/composables/panelPreview/previewMode'
import type { AnnotateDraft } from '@/composables/usePanelPreview'
import type { ChatAttachment } from '@/cx_types'
import type { PreviewLocate } from '@/lib/previewQuestion'
import type { RasterSelection } from './designRegion'
import type { MarkdownQuote, QuoteContext } from './markdownQuote'
import type { PreviewViewProps } from './previewViewProps'
import type { SlidePageContext } from './slidesContext'

import { computed, inject, ref, watch } from 'vue'

import { usePreviewImageRegion } from './usePreviewImageRegion'
import { usePreviewPagePin } from './usePreviewPagePin'
import { usePreviewPick } from './usePreviewPick'
import { usePreviewQuote } from './usePreviewQuote'

import { t } from '@/i18n'
import { firstCodePoints } from '@/lib/codePoints'
import { PENDING_NOTES, type PendingAnchor, type PendingNote } from '@/lib/pendingNotes'
import { fileName } from '@/lib/viewingContext'

interface LocatorDeps {
  /** 看 / 批注 / 改 三段模式（原型里那一段）。由宿主**显式传进来**，不靠 `inject`：
   *  `PanelPreviewView` 是在同一个 setup 里 `provide` 的，而 `inject` 只往祖先找——
   *  同一层提供的东西自己读不到（这条坑 `useRevealGate` 已经记过一次），照那样写
   *  「改」那一段挡指认的闸永远不生效。宿主没给（资料库、产物页那几处没有话题页）
   *  时按过去的行为走。 */
  mode: PreviewModeApi | null
  /** 指了一处，交给房间的对话。 */
  onLocate: (payload: PreviewLocate) => void
  /** 指了一处，私下问个人芝士：这句话只进自己和芝士那一条对话，不进这间房。 */
  onAskPersonal: (message: string) => void
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
  /** 看 / 批注 / 改 三段模式（原型里那一段）。有它时：
   *  - 「看」：拖选一段先在选区下浮一颗「添加批注」，点了才展开整张卡（两步，原型读模式）。
   *  - 「批注」：指一处直接出卡（一步，原型批注模式）。
   *  - 「改」：读者在打字，指一处什么都不做。
   *  没有这个口（资料库、产物页那几处）时按过去的行为走：指一处出卡，拖着选的那一段
   *  仍先出小按钮（`armFirst` 由外面给）。 */
  // 宿主那一份 `provide` 出去的和递给这里的是同一个对象（它在 `usePreviewLocator` 之前
  // 就建好了），所以直接拿着用；不包一层 getter——那会让下面的 `computed` 少一层依赖。
  const previewMode = deps.mode
  /** 这一段指不指得动。判据是「正文此刻真在编辑」而不是「模式停在改」：协同会话断了、
   *  编辑器自己关掉时，模式还停在「改」而正文已经回到只读——那时还按模式拦，读者会发现
   *  选中一段什么都不出，而屏幕上明明是可以读的正文。`annotating` 之外的两段都指得动，
   *  区别只在摆什么（看是先一颗小按钮、批注是直接出卡）。 */
  const pointing = computed(() => !previewMode || !previewMode.editing.value)

  /** 话题页提供的那一份待发篮。有它时逐条列在输入框上方、一起发；没有（资料库、产物页
   *  这些没有对话栏的地方）就退回本格自己面板底下那条老样式。 */
  const sharedPending = inject(PENDING_NOTES, null)
  /** 卡上「加入待发」旁边那颗键写几条：共享篮优先。 */
  const queuedCount = computed(() => (sharedPending ? sharedPending.notes.value.length : queued.value.length))
  /** 待发篮是否另有地方列出来（话题页那一份）。有它时本格不再自己画那条 sticky 条——
   *  同一批批注画两处，读者会以为进了两个地方。 */
  const pendingElsewhere = computed(() => !!sharedPending)
  /** 标注图传不上去时的那一句：不说的话，画完按了按钮看起来像什么都没发生。 */
  const annotateError = ref('')

  const imageRegion = usePreviewImageRegion(props, clearLocator)
  const pageLocator = usePreviewPagePin(props, {
    snapshot: async (page) => (await deps.snapshot(page)) ?? null,
    open: openLocator,
    clear: clearLocator,
    pointing: () => pointing.value,
  })
  const quoted = usePreviewQuote(props, (context) => pageLocator.canUse(context))

  // 网页预览里的圈选（判据和开关在 usePreviewPick）：有桥的网页由帧报回一处，应用由遮罩报回一块。
  const pick = usePreviewPick({
    frame: () => props.displayedFrame,
    previewUrl: () => props.previewUrl,
    onRuntime: deps.onPickMode,
    open: (label, quote, address) => openLocator(label, quote, address),
    quote: quoted,
    allowed: () => pointing.value,
  })

  function openLocator(label: string, quote: string, address: string, context?: QuoteContext) {
    // 「改」那一段里指一处什么都不弹：读者在正文里打字，拖选是选字，不是在批注。
    // 全部指法都收口到这里（选段、点格、框一块、钉一页、指一行、圈选），所以闸门只设
    // 这一处；页上那个钉和网页圈选是两个例外，它们会先动别的东西（截图、给帧发消息），
    // 各自另拦一道。
    if (!pointing.value) return
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

  /** 代码里指的几行：位置就写行号。源码不重排，行号就是芝士改它时找的那个地址——和
   *  markdown 按「哪一节」说位置正好相反（见上面那一支）。 */
  function onCodeLines(payload: { start: number; end: number; text: string }) {
    const where = codeLinesLabel(payload.start, payload.end)
    openLocator(where, firstCodePoints(payload.text, 200), where)
  }

  function onVideoLocate(payload: { seconds: number; timestamp: string }) {
    const where = t('work.room.video.timePoint', { time: payload.timestamp })
    openLocator(where, t('work.room.video.atTime', { time: payload.timestamp }), where)
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

  /** 攒着的这一批批注说的都是屏幕上这一版，行上写「第 N 版」。版本核不出来（清单里没有
   *  这一份、或这一版是清单之外改的）就不写——说错哪一版比不说更坏（同 `versions.ts`）。 */
  const pendingVersion = computed(() => {
    const seq = props.versions?.current.value?.seq
    if (!seq) return ''
    return t('work.room.versions.label', { seq })
  })

  /** 这一处指着文件里的哪儿——正文里那颗序号 pin 靠它找回来（见 `lib/pendingNotes`）。
   *
   *  能不能找回来各档不一样：markdown 存「原文 + 前后文」按字找；页上那一处存页码和
   *  比例；表里存单元格地址。哪一样都拿不到时（网页圈选、图上框选、代码行）就不画
   *  pin——那些地方本来也没有能长期成立的坐标，宁可不画。 */
  function pendingAnchor(): PendingAnchor | undefined {
    const page = quoted.page.value
    if (page) {
      if (page.scope !== 'selection') return undefined
      // 有比例位置就按点画（缩放、重排都跟着页走），没有才退回按字找。
      if (typeof page.x === 'number' && typeof page.y === 'number') {
        return { kind: 'page-pin', page: page.page, x: page.x, y: page.y }
      }
      return { kind: 'page-text', page: page.page, text: page.text, prefix: page.prefix, suffix: page.suffix }
    }
    const pickPayload = quoted.pick.value
    if (!pickPayload) return undefined
    if (pickPayload.kind === 'text-range') {
      return {
        kind: 'text-range',
        text: pickPayload.text,
        heading: pickPayload.heading,
        prefix: pickPayload.prefix,
        suffix: pickPayload.suffix,
      }
    }
    if (pickPayload.kind === 'sheet-cell') {
      return { kind: 'sheet-cell', sheet: pickPayload.sheet, address: pickPayload.address }
    }
    if (pickPayload.kind === 'sheet-range') {
      return { kind: 'sheet-cell', sheet: pickPayload.sheet, address: pickPayload.start }
    }
    return undefined
  }

  function queueLocator() {
    const target = locator.value
    const note = locatorNote.value.trim()
    if (!target || !note || pageLocator.pin.value) return
    const message = formatLocateMessage(target, note)
    if (sharedPending) {
      // 攒着的都是「交给芝士」那一路：只留评论的当场就挂上去了，不进待发篮
      //（卡上的「加入待发」也只在去处是芝士时出现）。
      sharedPending.add(pendingNoteFor(target, note, message))
      clearLocator()
      return
    }
    queued.value = [...queued.value, message]
    clearLocator()
  }

  /** 一条待发批注里与「是哪一份文件」有关的那几样。
   *
   *  收进篮里有两个入口（`queueLocator` 单条加、`sendQueued` 发之前先把当前这一处并进去），
   *  两处写的字段必须一样——只写一处、漏一处，新加的字段就会静默地一边有一边没有。 */
  function pendingNoteBase() {
    const path = props.previewFile?.path ?? ''
    return { file: fileName(path), path, version: pendingVersion.value, audience: 'cheese' as const }
  }

  function pendingNoteFor(
    target: { label: string; quote: string },
    note: string,
    message: string
  ): Omit<PendingNote, 'id'> {
    return {
      ...pendingNoteBase(),
      where: target.label,
      quote: target.quote,
      text: note,
      message,
      anchor: pendingAnchor(),
    }
  }

  function clearQueued() {
    if (sharedPending) sharedPending.clear()
    queued.value = []
  }

  function sendQueued() {
    if (sharedPending) {
      // 共享篮：还没收进篮里的这一处先并进去，再整批发。
      const target = locator.value
      const note = locatorNote.value.trim()
      if (target && note && !pageLocator.pin.value) {
        sharedPending.add(pendingNoteFor(target, note, formatLocateMessage(target, note)))
        clearLocator()
      }
      sharedPending.send('')
      return
    }
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
    // 卡片主按钮写着「全部发送（N+1）」时，这一下发的必须是整批：判据要和那颗键写字的
    // 判据同一条（`queuedCount`，共享篮优先），不是本格自己那一条——共享篮时
    // `queued.value` 永远是空的，用它会只发当前这一处、把篮里那几条留成没发出去。
    //
    // 图上框的那一块是例外：它这一下有自己的路（下面那一支），进的是「结构化引用」
    // 那条，篮里有几条与它无关——不分这一下，读者按「发送」会被换成一整批。
    if (!imageRegion.target.value && queuedCount.value > 0) {
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

  /** 去处选的是「问个人芝士」：同一句话和同一处位置，交给私聊，不进房间的对话。
   *
   *  不走 `quoted`（那条路把引用挂到房间的对话上），也不管待发篮——篮里攒的是「交给
   *  这间房的芝士」，私下问的几句合进去会发到错的地方。页上钉着的那一处照样按位置拼
   *  一句话发过去：个人芝士读不到房里的截图，要看的是页码和原文。 */
  function sendPersonalLocator() {
    const target = locator.value
    const note = locatorNote.value.trim()
    if (!target || !note) return
    deps.onAskPersonal(formatLocateMessage(target, note))
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
    /** 待发篮里现在有几条：共享篮优先（话题页那一份），没有就是本格自己攒的。 */
    queuedCount,
    /** 待发篮在别处列着（话题页输入框上方）：本格不再自己画那条。 */
    pendingElsewhere,
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
    sendPersonalLocator,
    onImageRegion,
    onPageContext,
    onQuote,
    onCell,
    onSheetRange,
    onMarkdownQuote,
    onCodeLines,
    onVideoLocate,
    onAnnotate,
  }
}

/** 「第 12 行」「第 12–18 行」：定位框上和发出去的那句话里都是它。 */
export function codeLinesLabel(start: number, end: number): string {
  return start === end
    ? t('work.room.preview.codeLine', { line: start })
    : t('work.room.preview.codeLines', { start, end })
}
