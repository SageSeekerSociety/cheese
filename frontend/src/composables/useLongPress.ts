import { type MaybeRefOrGetter, onScopeDispose, type Ref, ref, toValue, watch } from 'vue'

export interface LongPressOptions {
  /** 按住多久算长按，毫秒。 */
  delay?: number
  /** 手指挪开超过这么多像素就不算了（那是在滚动或拖动）。 */
  moveTolerance?: number
  /** 哪几种指针会触发。默认只认触摸和笔：桌面上右键和悬停菜单已经是这件事的入口。 */
  pointerTypes?: readonly string[]
  /** 暂时关掉（例如正在编辑这一行）。 */
  disabled?: MaybeRefOrGetter<boolean>
}

/**
 * 长按一个元素，打开它的操作——手机上「右键」的那个位置。
 *
 *   const row = ref<HTMLElement | null>(null)
 *   useLongPress(row, () => (sheetOpen.value = true))
 *
 * 规则：
 * - 按住 `delay`（500ms）不动才算；提前松手、指针被浏览器收走（开始滚动）、挪开超过
 *   `moveTolerance`（8px）都取消，所以正常的点按和滚动照旧。
 * - 真的触发了长按，紧跟着的那一下 click 被吞掉：不然松手时这一行还会被「点开」。
 *   没触发就什么都不吞。
 * - 按住期间关掉目标上的文字选择和 iOS 的长按预览，并拦下系统的右键菜单（Android
 *   长按会发 contextmenu）；松手后还原。
 *
 * 返回 `pressing`：手指正按在上面、还没到时间，可以用来画按下态。
 */
export function useLongPress(
  target: MaybeRefOrGetter<HTMLElement | null | undefined>,
  onLongPress: (event: PointerEvent) => void,
  options: LongPressOptions = {}
): { pressing: Ref<boolean> } {
  const delay = options.delay ?? 500
  const tolerance = options.moveTolerance ?? 8
  const pointerTypes = options.pointerTypes ?? ['touch', 'pen']

  const pressing = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  let startX = 0
  let startY = 0
  let pointerId: number | null = null
  let fired = false
  let restoreStyle: (() => void) | null = null

  function suppressSelection(el: HTMLElement) {
    const style = el.style as CSSStyleDeclaration & { webkitTouchCallout?: string; webkitUserSelect?: string }
    const previous = {
      userSelect: style.userSelect,
      webkitUserSelect: style.webkitUserSelect,
      webkitTouchCallout: style.webkitTouchCallout,
    }
    style.userSelect = 'none'
    style.webkitUserSelect = 'none'
    style.webkitTouchCallout = 'none'
    restoreStyle = () => {
      style.userSelect = previous.userSelect
      style.webkitUserSelect = previous.webkitUserSelect ?? ''
      style.webkitTouchCallout = previous.webkitTouchCallout ?? ''
      restoreStyle = null
    }
  }

  function cancel() {
    if (timer !== null) clearTimeout(timer)
    timer = null
    pointerId = null
    pressing.value = false
    // 触发了的那一次要等 click 过去再还原：iOS 在松手那一刻才决定要不要选中文字。
    if (!fired) restoreStyle?.()
  }

  function onPointerDown(event: PointerEvent) {
    if (toValue(options.disabled)) return
    if (!pointerTypes.includes(event.pointerType)) return
    if (event.pointerType === 'mouse' && event.button !== 0) return
    const el = event.currentTarget as HTMLElement
    cancel()
    restoreStyle?.()
    fired = false
    pointerId = event.pointerId
    startX = event.clientX
    startY = event.clientY
    pressing.value = true
    suppressSelection(el)
    timer = setTimeout(() => {
      timer = null
      pressing.value = false
      fired = true
      onLongPress(event)
    }, delay)
  }

  function onPointerMove(event: PointerEvent) {
    if (timer === null || event.pointerId !== pointerId) return
    if (Math.hypot(event.clientX - startX, event.clientY - startY) > tolerance) cancel()
  }

  function onPointerEnd(event: PointerEvent) {
    if (event.pointerId !== pointerId && timer !== null) return
    cancel()
    if (fired) {
      // click 在 pointerup 之后同一轮里派发；过了这一轮还没来就不会来了。
      setTimeout(() => {
        fired = false
        restoreStyle?.()
      }, 0)
    }
  }

  function onClick(event: MouseEvent) {
    if (!fired) return
    fired = false
    event.preventDefault()
    // 同一个元素上的 @click 也要拦住：长按的目标通常就是那一行本身。
    event.stopImmediatePropagation()
    restoreStyle?.()
  }

  function onContextMenu(event: Event) {
    if (pressing.value || fired) event.preventDefault()
  }

  // 长按打开的东西（面板、菜单）常常正好升到手指底下；松手时浏览器合成的那一下
  // click 落在它上面（多半是遮罩），刚打开就又关上。上面那条 onClick 只拦得住落回
  // 目标本身的 click，所以在 touchend 这一步就把合成 click 取消掉。
  function onTouchEnd(event: TouchEvent) {
    if (fired && event.cancelable) event.preventDefault()
  }

  let detach: (() => void) | null = null
  function attach(el: HTMLElement) {
    el.addEventListener('pointerdown', onPointerDown)
    el.addEventListener('pointermove', onPointerMove)
    el.addEventListener('pointerup', onPointerEnd)
    el.addEventListener('pointercancel', onPointerEnd)
    el.addEventListener('pointerleave', onPointerEnd)
    el.addEventListener('click', onClick, { capture: true })
    el.addEventListener('contextmenu', onContextMenu)
    el.addEventListener('touchend', onTouchEnd, { passive: false })
    detach = () => {
      el.removeEventListener('pointerdown', onPointerDown)
      el.removeEventListener('pointermove', onPointerMove)
      el.removeEventListener('pointerup', onPointerEnd)
      el.removeEventListener('pointercancel', onPointerEnd)
      el.removeEventListener('pointerleave', onPointerEnd)
      el.removeEventListener('click', onClick, { capture: true })
      el.removeEventListener('contextmenu', onContextMenu)
      el.removeEventListener('touchend', onTouchEnd)
      detach = null
    }
  }

  watch(
    () => toValue(target),
    (el) => {
      detach?.()
      cancel()
      fired = false
      restoreStyle?.()
      if (el) attach(el)
    },
    { immediate: true, flush: 'post' }
  )

  onScopeDispose(() => {
    detach?.()
    if (timer !== null) clearTimeout(timer)
    restoreStyle?.()
  })

  return { pressing }
}
