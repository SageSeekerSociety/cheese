// 标注的键盘路线：撤销/重做、删除、退出，以及按住空格平移。
//
// 从 DesignImage.vue 里搬出来的——那些键都挂在 window 上（图未必拿着焦点），所以这里
// 自己挂、自己摘。要什么状态由外面传进来：这个组合式函数不改笔画数据，只决定「这一下
// 该交给谁」。
import type { Ref } from 'vue'
import type { SketchTool } from './designSketch'

import { onBeforeUnmount, onMounted } from 'vue'

export interface DesignShortcutsOptions {
  /** 这一格正显示着没有；收起来的那几页（工作面板用 v-show 留着的）照样挂着 listener，但它们没在看图。 */
  active: () => boolean
  /** 空格按住时给平移：这个组合式函数只负责按住/松开。 */
  spaceHeld: Ref<boolean>
  tool: Ref<SketchTool>
  /** 选中的是哪一条笔画（按索引）。 */
  selectedStroke: Ref<number | null>
  /** 正在改的那条已有文字；有它说明这次 Esc 该作废的是这次文字编辑。 */
  editingText: Ref<{ index: number; value: string } | null>
  /** 图上有没有正在打字的输入框（新画的或改已有的）。 */
  textEditing: Ref<boolean>
  cancelTextEdit: () => void
  /** 作废画布手里那一笔（正在新画一条文字，或画到一半）。 */
  cancelCanvas: () => void
  /** 正在画或正在拖：撤销/重做这时一律拒绝。 */
  busy: Ref<boolean>
  canUndo: Ref<boolean>
  canRedo: Ref<boolean>
  undo: () => void
  redo: () => void
  cancelGesture: () => void
  deleteSelected: () => void
  /** 光标交还默认的框选。 */
  exitSelection: () => void
}

export function useDesignShortcuts(options: DesignShortcutsOptions) {
  const {
    active,
    spaceHeld,
    tool,
    selectedStroke,
    editingText,
    textEditing,
    cancelTextEdit,
    cancelCanvas,
    busy,
    canUndo,
    canRedo,
    undo,
    redo,
    cancelGesture,
    deleteSelected,
    exitSelection,
  } = options

  function isTyping(target: EventTarget | null) {
    const element = target as HTMLElement | null
    if (!element) return false
    return (
      element.isContentEditable === true ||
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName) ||
      element.closest?.('input, textarea, [contenteditable="true"]') != null
    )
  }
  /**
   * Esc 退出正在进行的操作，做掉了就报 true。
   *
   * 正在图上打字：这次文字作废，画笔留着，方便重打。否则是画到一半（或只是挑着一支
   * 画笔）：手里的这一笔作废，光标交还默认的框选。已经框好的那一块不在这里动——它归
   * 外面那条统一的定位卡管（`PreviewLocator` 自己接 Esc）。
   */
  function exitCurrent(): boolean {
    if (textEditing.value) {
      if (editingText.value) cancelTextEdit()
      else cancelCanvas()
      return true
    }
    if (tool.value === 'select') {
      // select 工具下手里还挑着一笔：Esc 放下它。
      if (selectedStroke.value !== null) {
        selectedStroke.value = null
        return true
      }
      return false
    }
    cancelCanvas()
    exitSelection()
    return true
  }
  /**
   * 标注的键盘快捷键：撤销/重做/退出。
   *
   * 焦点落在输入框里时一律让开——那个「说一句要改什么」的框、图上的文字框，都要能用
   * 自己的编辑键（含它们各自的撤销）。没有可撤销/可退的东西时也不拦，键照旧交回浏览器。
   */
  function keyDown(event: KeyboardEvent) {
    if (isTyping(event.target)) return
    // 收起来的那几页（工作面板用 v-show 留着的）照样挂着这个 listener，但它们没在看
    // 图：空格该去翻页，⌘Z 也不该去动一张没人看着的图。
    if (!active()) return
    const mod = event.metaKey || event.ctrlKey
    // ⌘/Ctrl+Z 撤销；⇧⌘/Ctrl+Z 与 Ctrl+Y 重做。认 key 不认 code：撤销绑的是 Z 这个字母。
    if (mod && event.key.toLowerCase() === 'z') {
      // 正在画、正在拖的时候，撤销/重做一律拒绝：手上这一笔还没落定，历史不好动。
      if (busy.value) return
      if (event.shiftKey ? canRedo.value : canUndo.value) {
        event.preventDefault()
        if (event.shiftKey) redo()
        else undo()
      }
      return
    }
    if (mod && event.key.toLowerCase() === 'y') {
      if (busy.value) return
      if (canRedo.value) {
        event.preventDefault()
        redo()
      }
      return
    }
    // Backspace / Delete 删掉选中的那一笔。
    if (event.key === 'Backspace' || event.key === 'Delete') {
      // 手上还按着拖动时，先把这个手势收掉再删。不收就直接删，松手时 sheetUp 会拿着
      // 已经不存在的 index 再记一条历史，撤销栈错位；而删除是明确的意图，不该被拖动作废。
      cancelGesture()
      if (selectedStroke.value !== null) {
        event.preventDefault()
        deleteSelected()
      }
      return
    }
    if (event.key === 'Escape') {
      // 没做到事就不拦：Esc 在浏览器里还管着退出全屏这类事。
      if (exitCurrent()) event.preventDefault()
      return
    }
    if (event.code !== 'Space') return
    // 空格在浏览器里是翻页，按住时要把它让给平移。
    event.preventDefault()
    spaceHeld.value = true
  }
  function keyUp(event: KeyboardEvent) {
    if (event.code === 'Space') spaceHeld.value = false
  }

  onMounted(() => {
    window.addEventListener('keydown', keyDown)
    window.addEventListener('keyup', keyUp)
  })
  onBeforeUnmount(() => {
    window.removeEventListener('keydown', keyDown)
    window.removeEventListener('keyup', keyUp)
  })
}
