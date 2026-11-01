// 标注那一半：笔画数据、内容分界线、说明文字，以及「把画好的这张交出去」。
//
// 从 DesignImage.vue 里搬出来的——对象级编辑（选中、拖动/缩放、改色、删除、文字编辑、
// 撤销历史）本来就在 `useSketchObjectEdit`，这里把围着它的那几件也算进来：点内容块的落笔、
// 「画了但还没发出去」的判定、合成并发出去。屏幕上怎么画还是组件的事。
import type { Ref } from 'vue'
import type { Point } from './designRegion'
import type { SketchStroke, SketchTool } from './designSketch'
import type { ContentProfile } from './designSnap'

import { computed, ref, watch } from 'vue'

import { composeSketch, sampleImage, strokeWidth } from './designSketch'
import { blockAt, blockUnder, contentProfile } from './designSnap'
import { useSketchObjectEdit } from './useSketchObjectEdit'

import { t } from '@/i18n'

/** 画完的那张合成图；上传和发消息由外面做，这里只管把它做出来。 */
export interface AnnotatePayload {
  blob: Blob
  filename: string
  naturalWidth: number
  naturalHeight: number
  count: number
  note: string
}

export interface DesignAnnotationsOptions {
  image: Ref<HTMLImageElement | null>
  natural: Ref<{ width: number; height: number }>
  scale: Ref<number>
  tool: Ref<SketchTool>
  color: Ref<string>
  /** 画布层此刻自己有没有在忙（画到一半）。 */
  canvasBusy: () => boolean
  /** 原图文件名，用来给合成出来的那张起名。 */
  alt: () => string
  /** 屏幕上的图转过、翻过没有：笔画按原图像素记，命中测试要先折回去。 */
  adjust?: () => { rotate: number; flip: boolean }
  /** 合成好了，交回给组件去发 `annotate`。 */
  emitAnnotate: (payload: AnnotatePayload) => void
}

export function useDesignAnnotations(options: DesignAnnotationsOptions) {
  const { image, natural, scale, tool, color, canvasBusy, alt, emitAnnotate } = options

  /** 图片当前在屏幕上的几何：命中测试与手势都拿它把显示像素换算成原图像素。 */
  function imageGeometry() {
    const element = image.value
    if (!element || !natural.value.width || !natural.value.height) return null
    const rect = element.getBoundingClientRect()
    if (!rect.width || !rect.height) return null
    return {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
      naturalWidth: natural.value.width,
      naturalHeight: natural.value.height,
      scale: scale.value,
      adjust: options.adjust?.(),
    }
  }

  const {
    strokes,
    selectedStroke,
    editingText,
    textEditing: editingExistingText,
    editField,
    canUndo,
    canRedo,
    busy,
    editStyle,
    addStroke,
    undo,
    redo,
    clearStrokes,
    dropAnnotations,
    reset: resetStrokes,
    recolor,
    restyle,
    redactStyle,
    redactToolbar,
    redactToolbarStyle,
    deleteSelected,
    cancelGesture,
    commitTextEdit,
    cancelTextEdit,
    openTextEditor,
    onEditEnter,
    onEditEscape,
    sheetDown,
    sheetMove,
    sheetUp,
    sheetCancel,
    sheetDoubleClick,
  } = useSketchObjectEdit({
    geometry: imageGeometry,
    natural,
    scale,
    tool,
    color,
    canvasBusy,
  })

  const note = ref('')
  const profile = ref<ContentProfile | null>(null)
  /** 画布自带的那个输入框开着（正在**新画**一条文字；改已有的那条走 editingExistingText）。 */
  const drawingText = ref(false)
  const exporting = ref(false)
  /** 合成失败时的那一句：它发生在这里（画布在这一层），上传失败那句在外面。 */
  const sendError = ref('')
  const sentStrokes = ref<SketchStroke[] | null>(null)

  /**
   * 图上有没有正在打字的输入框：新画的（画布自己那个）或改已有的（`editingExistingText`）。
   *
   * 两条路各有一个开关，但对外只该有一个说法——发消息要拦住、Esc 要先收输入框、改文字
   * 期间区域选择器要整个不挂，这三处都只关心「有没有在打字」，不关心是哪条路进来的。
   */
  const textEditing = computed(() => drawingText.value || editingExistingText.value)
  const penWidth = computed(() => strokeWidth(natural.value.width))
  const canSend = computed(() => strokes.value.length > 0 && note.value.trim().length > 0 && !textEditing.value)

  /**
   * 有没有「画了但还没发出去」的笔画。
   *
   * 发出去之后笔画还留在屏上（人看着自己刚标的东西），但那些不再算「会丢的东西」——
   * 所以不是「有笔画就拦」，而是「有笔画、且屏上跟发出去的那份不一样」才拦（见
   * `annotationDiscard`）。
   *
   * 判据是「上一次发出去时的那几笔」，不是一个开关：发过之后撤销再重做，屏上又跟发
   * 出去时一样了，不该再被当成欠着谁。撤销/重做拿回来的是同一批笔画对象（快照只换外
   * 层数组，笔画对象本身是引用），所以逐条按引用比就够，不必深比较。
   */
  function matchesSent(current: SketchStroke[]): boolean {
    const sent = sentStrokes.value
    return sent !== null && current.length === sent.length && current.every((stroke, index) => stroke === sent[index])
  }
  const unsent = computed(() => strokes.value.length > 0 && !matchesSent(strokes.value))
  /** 真会丢掉东西的时候（画了、且还没发），才值得拦一道。 */
  const wouldLoseStrokes = computed(() => strokes.value.length > 0 && unsent.value)

  /** 内容分界线读一次就够：它只随图和版本变，不随缩放变。 */
  function readProfile(current: HTMLImageElement) {
    const sample = sampleImage(current)
    profile.value = sample ? contentProfile(sample, current.naturalWidth, current.naturalHeight) : null
  }

  /**
   * 点一下内容块：不画东西，直接拿光标下那一块，按当前工具落成一条笔画。
   *
   * 两种工具不一样：
   *
   * - 矩形 / 椭圆：点在空白处就框住整张图（画个圈，看得出来，也随手能删）。
   * - 涂黑：点在空白处什么都不做。涂黑是盖住，猜出来的整张图会被真的涂掉，
   *   而这一下只是点到空白——没有框就不猜。
   */
  function pickBlock(point: Point) {
    const bounds = profile.value
    if (!bounds || !natural.value.width) return
    const active = tool.value
    if (active === 'redact') {
      const hit = blockUnder(point, bounds)
      if (!hit) return
      addStroke({
        tool: 'redact',
        color: color.value,
        width: penWidth.value,
        region: hit,
        redactStyle: redactStyle.value,
      })
      return
    }
    if (active !== 'rect' && active !== 'ellipse') return
    addStroke({
      tool: active,
      color: color.value,
      width: penWidth.value,
      region: blockAt(point, bounds, natural.value.width, natural.value.height),
    })
  }
  async function sendAnnotated() {
    const current = image.value
    if (!current || !strokes.value.length || exporting.value) return
    exporting.value = true
    sendError.value = ''
    try {
      const blob = await composeSketch(current, strokes.value)
      // 合成不出来（画布不可用，或者压到最小还是太大）时说一声：不说的话，按了按钮
      // 看着像什么都没发生。
      if (!blob) {
        sendError.value = t('design.composeFailed')
        return
      }
      const base = alt().replace(/\.[^.]+$/, '') || 'image'
      emitAnnotate({
        blob,
        filename: `${base}-annotated.png`,
        naturalWidth: natural.value.width,
        naturalHeight: natural.value.height,
        count: strokes.value.length,
        note: note.value.trim(),
      })
      // 交出去了：屏上还留着，但记下发出去时是哪几笔——再走就不欠谁的了
      // （撤销再重做回到同一个样子也不算欠）。
      sentStrokes.value = [...strokes.value]
    } catch (error) {
      sendError.value = error instanceof Error ? error.message : t('design.composeFailed')
    } finally {
      exporting.value = false
    }
  }
  /** 图或版本换了：笔画、说明、内容分界线都跟着作废。 */
  function reset() {
    resetStrokes()
    sentStrokes.value = null
    note.value = ''
    profile.value = null
  }

  // 换到会画的工具就放下选中的那一条：那块把手不该再盖在画布上抢指针。
  // 切回 select（含「画完自动选中」那一下）时留着选中——那是刚画完的那一笔。
  watch(tool, (next) => {
    if (next !== 'select') {
      if (editingExistingText.value) commitTextEdit()
      selectedStroke.value = null
    }
  })

  return {
    strokes,
    selectedStroke,
    editingText,
    editField,
    canUndo,
    canRedo,
    busy,
    editStyle,
    addStroke,
    undo,
    redo,
    clearStrokes,
    dropAnnotations,
    recolor,
    restyle,
    redactStyle,
    redactToolbar,
    redactToolbarStyle,
    deleteSelected,
    cancelGesture,
    commitTextEdit,
    cancelTextEdit,
    openTextEditor,
    onEditEnter,
    onEditEscape,
    sheetDown,
    sheetMove,
    sheetUp,
    sheetCancel,
    sheetDoubleClick,
    note,
    profile,
    drawingText,
    textEditing,
    penWidth,
    canSend,
    exporting,
    sendError,
    wouldLoseStrokes,
    readProfile,
    pickBlock,
    sendAnnotated,
    reset,
  }
}
