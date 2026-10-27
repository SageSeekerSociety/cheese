// 列表右边那一栏预览可以拖宽，宽度按占父容器的比例记在这台浏览器上。
//
// 记比例不记像素：同一个人换一块屏幕、把窗口拉宽，预览还是占他上次选的那一份。
// 默认一半——资料库里点开一份是为了读它，列表只需要留够认出文件名的宽度。最窄由样式
// 管（`min-width`），这里只管比例的上下限，免得一下拖没了列表或预览。
import type { Ref } from 'vue'

import { computed, ref } from 'vue'

import { storageGet, storageSet } from '@/lib/browserStorage'

const MIN = 0.3
const MAX = 0.75
const STEP = 0.05

const clamp = (v: number) => Math.min(MAX, Math.max(MIN, v))

export function usePaneWidth(key: string, pane: Ref<HTMLElement | null>, fallback = 0.5) {
  const saved = Number(storageGet(key))
  const share = ref(Number.isFinite(saved) && saved > 0 ? clamp(saved) : fallback)

  function set(next: number) {
    share.value = clamp(next)
    storageSet(key, share.value.toFixed(3))
  }

  function startDrag(event: PointerEvent) {
    const parent = pane.value?.parentElement
    if (!parent) return
    event.preventDefault()
    const box = parent.getBoundingClientRect()
    const move = (e: PointerEvent) => set((box.right - e.clientX) / box.width)
    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
  }

  // 键盘也能调：把手是一个 separator，左右键各挪一格，Home 回到一半。
  function onKey(event: KeyboardEvent) {
    if (event.key === 'ArrowLeft') set(share.value + STEP)
    else if (event.key === 'ArrowRight') set(share.value - STEP)
    else if (event.key === 'Home') set(fallback)
    else return
    event.preventDefault()
  }

  const style = computed(() => ({ width: `${(share.value * 100).toFixed(1)}%` }))
  const percent = computed(() => Math.round(share.value * 100))
  return { share, style, percent, startDrag, onKey, set }
}
