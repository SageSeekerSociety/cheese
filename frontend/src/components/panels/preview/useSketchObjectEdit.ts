// 对象级编辑的「数据那一半」：选中、拖动/缩放、改色、删除、文字编辑，以及撤销历史。
//
// 从 DesignImage.vue 里搬出来的——那个组件本来就贴着 file-size 闸门的边，再加这一层
// 就过不去了（见 .claude/rules/architecture.md）。屏幕上怎么画、图片怎么量、键盘怎么
// 路由都留在组件里；这里只认「笔画这份数据该怎么被改」，换算坐标用的几何由外面传进来。
import type { Ref } from 'vue'
import type { Point } from './designRegion'
import type { SketchStroke, SketchTool } from './designSketch'
import type { HandleRole } from './designSketchSelection'

import { computed, nextTick, ref } from 'vue'

import { fontSize } from './designSketch'
import { hitTest } from './designSketchHit'
import {
  applyResize,
  canResize,
  handleGrabRadius,
  isEmptyStroke,
  isSelectableStroke,
  moveStroke,
  nearestHandle,
  strokeHandles,
} from './designSketchSelection'

/** 显示像素 ↔ 原图像素的换算；组件由图元素量出来。 */
export type SketchGeometry = { left: number; top: number; scale: number }

/** 一次按下开始的手势：拖动整笔，或拖某个把手缩放。松手时若动过才记一条历史。 */
type Gesture = {
  kind: 'move' | 'resize'
  role: HandleRole | null
  index: number
  origin: Exclude<SketchStroke, { tool: 'pen' }>
  before: SketchStroke[]
  /** 原图像素：按下时指针落在哪儿，用来算位移。 */
  start: Point
  /** 屏幕像素：按下时指针落在哪儿，用来算 3px / 10px 门槛。 */
  client: Point
  touch: boolean
  started: boolean
}

/**
 * 撤销栈：一整份快照一条，上限 100。
 *
 * 拖动/缩放只在**松手且真的动过**时才在松手那一刻记一条，移动途中不记；加一笔、删除、
 * 改色、文字编辑提交、清空各算一条。
 */
const HISTORY_LIMIT = 100

export function useSketchObjectEdit(options: {
  geometry: () => SketchGeometry | null
  natural: Ref<{ width: number; height: number }>
  scale: Ref<number>
  tool: Ref<SketchTool>
  color: Ref<string>
  /** 画布层此刻自己有没有在忙（画到一半）；有的话撤销/重做要拒绝。 */
  canvasBusy: () => boolean
}) {
  const { geometry, natural, scale, tool, color, canvasBusy } = options

  const strokes = ref<SketchStroke[]>([])
  const undoStack = ref<SketchStroke[][]>([])
  const redoStack = ref<SketchStroke[][]>([])
  /** 选中的是哪一条（按索引）；除自由笔外都能选中、编辑。 */
  const selectedStroke = ref<number | null>(null)
  /** 正在编辑的那条已有文字：原文字不画，由输入框呈现。 */
  const editingText = ref<{ index: number; value: string } | null>(null)
  /** 文字编辑框换过显隐：发消息、退出这些动作要看它。 */
  const textEditing = ref(false)
  /** 正在拖/缩一笔（挂在 sheet 上的手势）：撤销/重做这时要拒绝。 */
  const interacting = ref(false)
  const editField = ref<HTMLInputElement | null>(null)

  let gesture: Gesture | null = null
  let gesturePointer: number | null = null

  const canUndo = computed(() => undoStack.value.length > 0)
  const canRedo = computed(() => redoStack.value.length > 0)
  /** 正在画或正在拖的时候，撤销/重做要被拒绝。 */
  const busy = computed(() => interacting.value || canvasBusy())
  /** 选中的那一笔。 */
  const selectedShape = computed(() => {
    const index = selectedStroke.value
    if (index === null) return null
    const stroke = strokes.value[index]
    return stroke && isSelectableStroke(stroke) ? stroke : null
  })

  /** 记一条历史快照（整份笔画数组），并把重做栈清掉。 */
  function pushHistory(before: SketchStroke[]) {
    undoStack.value = [...undoStack.value, before].slice(-HISTORY_LIMIT)
    redoStack.value = []
  }
  function addStroke(stroke: SketchStroke) {
    const before = strokes.value
    strokes.value = [...before, stroke]
    pushHistory(before)
    // 画完一笔非自由笔的图形就选中它、把工具交回 select；自由笔连着画，不打断。
    if (stroke.tool !== 'pen') {
      selectedStroke.value = strokes.value.length - 1
      tool.value = 'select'
    }
  }
  function undo() {
    const before = undoStack.value.at(-1)
    if (!before) return
    redoStack.value = [...redoStack.value, strokes.value]
    undoStack.value = undoStack.value.slice(0, -1)
    strokes.value = before
    selectedStroke.value = null
  }
  function redo() {
    const next = redoStack.value.at(-1)
    if (!next) return
    undoStack.value = [...undoStack.value, strokes.value]
    redoStack.value = redoStack.value.slice(0, -1)
    strokes.value = next
    selectedStroke.value = null
  }
  function clearStrokes() {
    if (!strokes.value.length) return
    pushHistory(strokes.value)
    strokes.value = []
    selectedStroke.value = null
  }
  /** 丢弃那次「放弃这些标注？」：连历史一起清掉，不给撤回来的机会。 */
  function dropAnnotations() {
    strokes.value = []
    undoStack.value = []
    redoStack.value = []
    selectedStroke.value = null
    editingText.value = null
    textEditing.value = false
  }
  /** 换图/换版本：屏上的笔画和历史都不属于新图。 */
  function reset() {
    strokes.value = []
    undoStack.value = []
    redoStack.value = []
    selectedStroke.value = null
    editingText.value = null
    textEditing.value = false
    gesture = null
    gesturePointer = null
    interacting.value = false
  }
  /** 改选中对象的颜色；没选中就只改「下一笔的颜色」。 */
  function recolor(swatch: string) {
    const index = selectedStroke.value
    const stroke = index === null ? null : strokes.value[index]
    if (stroke && isSelectableStroke(stroke)) {
      const before = strokes.value
      strokes.value = before.map((current, at) => (at === index ? { ...current, color: swatch } : current))
      pushHistory(before)
    }
    color.value = swatch
  }
  /** Backspace / Delete 删掉选中的那一笔。 */
  function deleteSelected() {
    const index = selectedStroke.value
    if (index === null || !strokes.value[index]) return
    const before = strokes.value
    strokes.value = before.filter((_, at) => at !== index)
    pushHistory(before)
    selectedStroke.value = null
  }

  function beginGesture(
    index: number,
    role: HandleRole | null,
    kind: 'move' | 'resize',
    event: PointerEvent,
    box: SketchGeometry
  ) {
    const stroke = strokes.value[index]
    if (!stroke || !isSelectableStroke(stroke)) return
    gesture = {
      kind,
      role,
      index,
      origin: stroke,
      before: strokes.value,
      start: { x: (event.clientX - box.left) / box.scale, y: (event.clientY - box.top) / box.scale },
      client: { x: event.clientX, y: event.clientY },
      touch: event.pointerType === 'touch',
      started: false,
    }
    gesturePointer = event.pointerId
    interacting.value = true
    ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
    event.preventDefault()
    event.stopPropagation()
  }

  /**
   * 选中工具下按下：先看把手，再看对象，都没中就放下选中、把指针让给区域选择器。
   *
   * 用 capture 拦截：区域选择器是 sheet 的子层，不先拦一道，它会先吃掉这一下。
   */
  function sheetDown(event: PointerEvent) {
    if (event.button !== 0 || tool.value !== 'select') return
    // 正在编辑文字：这一层不能抢。输入框是 sheet 的子元素，capture 阶段祖先的监听
    // 先跑，输入框上的 @pointerdown.stop 拦不住它 —— 不特判就会出现「点不进编辑框
    // 放光标、手一抖反而把这条文字拖走」。点在框内交给输入框；点在别处当成编辑结束，
    // 并把这一下吃掉，别顺手开始框区域。区域选择器在编辑期间整个不挂（见模板上的
    // `:enabled`），所以「别顺手开始框区域」是结构上就成立的，不指望下面这句
    // stopPropagation —— 测试环境里它拦不住子元素上的监听。
    if (editingText.value) {
      const field = editField.value
      if (field && event.target instanceof Node && field.contains(event.target)) return
      commitTextEdit()
      event.preventDefault()
      event.stopPropagation()
      return
    }
    const box = geometry()
    if (!box) return
    const display = { x: event.clientX - box.left, y: event.clientY - box.top }
    const touch = event.pointerType === 'touch'
    const current = selectedShape.value
    const currentIndex = selectedStroke.value
    if (current && currentIndex !== null) {
      const grab = handleGrabRadius(current, box.scale, natural.value.width)
      const role = nearestHandle(strokeHandles(current, box.scale, natural.value.width), display, grab)
      if (role) {
        // 文字没有可缩的框：拖它的把手等于平移。
        beginGesture(currentIndex, role, canResize(current) ? 'resize' : 'move', event, box)
        return
      }
    }
    const index = hitTest(strokes.value, display, {
      touch,
      scale: box.scale,
      naturalWidth: natural.value.width,
    })
    if (index === null) {
      // 点在空白处：放下选中，让指针落到下面的区域选择器上（交给芝士）。
      selectedStroke.value = null
      return
    }
    selectedStroke.value = index
    beginGesture(index, null, 'move', event, box)
  }

  function sheetMove(event: PointerEvent) {
    const active = gesture
    if (!active || event.pointerId !== gesturePointer) return
    const box = geometry()
    if (!box) return
    if (!active.started) {
      const moved = Math.hypot(event.clientX - active.client.x, event.clientY - active.client.y)
      if (moved < (active.touch ? 10 : 3)) return
      active.started = true
    }
    const point = { x: (event.clientX - box.left) / box.scale, y: (event.clientY - box.top) / box.scale }
    const next =
      active.kind === 'resize' && active.role
        ? applyResize(active.origin, active.role, point, event.shiftKey)
        : moveStroke(
            active.origin,
            point.x - active.start.x,
            point.y - active.start.y,
            natural.value.width,
            natural.value.height
          )
    strokes.value = strokes.value.map((stroke, index) => (index === active.index ? next : stroke))
    event.preventDefault()
    event.stopPropagation()
  }

  function sheetUp(event: PointerEvent) {
    const active = gesture
    if (!active || (gesturePointer !== null && event.pointerId !== gesturePointer)) return
    gesture = null
    gesturePointer = null
    interacting.value = false
    if (!active.started) return
    const moved = strokes.value[active.index]
    if (moved && isEmptyStroke(moved)) {
      // 拖到退化等于「这一笔没了」——和 Backspace 删掉一样要能撤销，先记一条再删。
      pushHistory(active.before)
      strokes.value = strokes.value.filter((_, index) => index !== active.index)
      selectedStroke.value = null
      return
    }
    pushHistory(active.before)
  }

  /** 手势被打断（指针取消）：退回按下前的样子，不记历史。 */
  function sheetCancel(event: PointerEvent) {
    const active = gesture
    if (!active || (gesturePointer !== null && event.pointerId !== gesturePointer)) return
    gesture = null
    gesturePointer = null
    interacting.value = false
    if (active.started) strokes.value = active.before
  }

  /** 放下手里的手势，不动数据。删除键要用：不然松手时会拿着已经没了的 index 记历史。 */
  function cancelGesture() {
    gesture = null
    gesturePointer = null
    interacting.value = false
  }

  /** select 工具下双击文字进编辑。 */
  function sheetDoubleClick(event: MouseEvent) {
    if (tool.value !== 'select') return
    const box = geometry()
    if (!box) return
    const display = { x: event.clientX - box.left, y: event.clientY - box.top }
    const index = hitTest(strokes.value, display, { scale: box.scale, naturalWidth: natural.value.width })
    if (index === null) return
    if (strokes.value[index]?.tool !== 'text') return
    openTextEditor(index)
  }

  /** 编辑一条已有文字：原文字不画，由这个输入框呈现。 */
  function openTextEditor(index: number) {
    const stroke = strokes.value[index]
    if (!stroke || stroke.tool !== 'text') return
    if (editingText.value?.index === index) return
    if (editingText.value) commitTextEdit()
    tool.value = 'select'
    selectedStroke.value = index
    editingText.value = { index, value: stroke.text }
    textEditing.value = true
    void nextTick(() => {
      editField.value?.focus()
      editField.value?.select()
    })
  }
  /** 提交文字编辑；trim 为空就把这条文字对象删掉。 */
  function commitTextEdit() {
    const edit = editingText.value
    if (!edit) return
    editingText.value = null
    textEditing.value = false
    const before = strokes.value
    const stroke = before[edit.index]
    if (!stroke || stroke.tool !== 'text') return
    const text = edit.value.trim()
    if (!text) {
      strokes.value = before.filter((_, index) => index !== edit.index)
      pushHistory(before)
      selectedStroke.value = null
      return
    }
    if (text === stroke.text) return
    strokes.value = before.map((current, index) => (index === edit.index ? { ...stroke, text } : current))
    pushHistory(before)
  }
  /** 取消文字编辑：原文照旧留着。 */
  function cancelTextEdit() {
    if (!editingText.value) return
    editingText.value = null
    textEditing.value = false
  }
  function onEditEnter(event: KeyboardEvent) {
    event.stopPropagation()
    if (event.isComposing || event.keyCode === 229) return
    event.preventDefault()
    commitTextEdit()
  }
  function onEditEscape(event: KeyboardEvent) {
    event.stopPropagation()
    if (event.isComposing || event.keyCode === 229) return
    event.preventDefault()
    cancelTextEdit()
  }

  const editStyle = computed(() => {
    const edit = editingText.value
    const stroke = edit ? strokes.value[edit.index] : null
    if (!edit || stroke?.tool !== 'text') return {}
    return {
      left: `${stroke.at.x * scale.value}px`,
      top: `${stroke.at.y * scale.value}px`,
      color: stroke.color,
      fontSize: `${fontSize(natural.value.width) * scale.value}px`,
    }
  })

  return {
    strokes,
    undoStack,
    redoStack,
    selectedStroke,
    editingText,
    textEditing,
    interacting,
    editField,
    canUndo,
    canRedo,
    busy,
    selectedShape,
    editStyle,
    addStroke,
    undo,
    redo,
    clearStrokes,
    dropAnnotations,
    reset,
    recolor,
    deleteSelected,
    cancelGesture,
    commitTextEdit,
    openTextEditor,
    cancelTextEdit,
    onEditEnter,
    onEditEscape,
    sheetDown,
    sheetMove,
    sheetUp,
    sheetCancel,
    sheetDoubleClick,
  }
}
