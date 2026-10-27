// 列表右边那一栏预览可以拖宽，宽度按占父容器的比例记在这台浏览器上。
//
// 记比例不记像素：同一个人换一块屏幕、把窗口拉宽，预览还是占他上次选的那一份。
// 默认一半——资料库里点开一份是为了读它，列表只需要留够认出文件名的宽度。
//
// 下限有两层：比例不低于 30%，宽度也不低于 `MIN_PX`（再窄预览里的文档就没法读了）。
// 父容器窄的时候后一层更紧，下限按当时的父宽换算成比例，所以拖到底就停在真能到的地方，
// 读屏念出来的 aria-valuemin 和存下来的值也是这一个，不会记一个永远到不了的宽度。
import type { Ref } from 'vue'

import { computed, onScopeDispose, ref } from 'vue'

import { storageGet, storageSet } from '@/lib/browserStorage'

export const PANE_MIN = 0.3
export const PANE_MAX = 0.75
const MIN_PX = 480
const STEP = 0.05

export function usePaneWidth(key: string, pane: Ref<HTMLElement | null>, fallback = 0.5) {
  const parentWidth = ref(0)
  const floor = computed(() =>
    parentWidth.value > 0 ? Math.min(PANE_MAX, Math.max(PANE_MIN, MIN_PX / parentWidth.value)) : PANE_MIN
  )
  const clamp = (v: number) => Math.min(PANE_MAX, Math.max(floor.value, v))

  const saved = Number(storageGet(key))
  const share = ref(Number.isFinite(saved) && saved > 0 ? Math.min(PANE_MAX, Math.max(PANE_MIN, saved)) : fallback)

  function measure(): DOMRect | null {
    const box = pane.value?.parentElement?.getBoundingClientRect() ?? null
    if (box && box.width > 0) parentWidth.value = box.width
    return box
  }

  function save() {
    storageSet(key, share.value.toFixed(3))
  }

  /** 改到某一份并记下（键盘、双击走这条；拖动中途只改不记，松手才记一次）。 */
  function set(next: number) {
    measure()
    share.value = clamp(next)
    save()
  }

  function reset() {
    set(fallback)
  }

  let release: (() => void) | null = null
  function startDrag(event: PointerEvent) {
    const box = measure()
    if (!box) return
    event.preventDefault()
    const move = (e: PointerEvent) => {
      share.value = clamp((box.right - e.clientX) / box.width)
    }
    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
      release = null
      save()
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
    // 触摸被打断、窗口失焦时不会有 pointerup：取消也算松手，否则监听一直挂着。
    window.addEventListener('pointercancel', stop)
    release = stop
  }
  // 拖到一半整栏被关掉（换了文件、切到手机布局）：照样摘掉监听。
  onScopeDispose(() => release?.())

  // 键盘也能调：把手是一个 separator，左右键各挪一格，Home 回到默认。
  function onKey(event: KeyboardEvent) {
    if (event.key === 'ArrowLeft') set(share.value + STEP)
    else if (event.key === 'ArrowRight') set(share.value - STEP)
    else if (event.key === 'Home') reset()
    else return
    event.preventDefault()
  }

  const style = computed(() => ({ width: `${(share.value * 100).toFixed(1)}%` }))
  const percent = computed(() => Math.round(share.value * 100))
  const minPercent = computed(() => Math.round(floor.value * 100))
  const maxPercent = Math.round(PANE_MAX * 100)
  return { share, style, percent, minPercent, maxPercent, startDrag, onKey, set, reset }
}
