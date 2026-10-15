import { readonly, ref } from 'vue'

import { storageGet, storageSet } from '@/lib/browserStorage'

// 侧栏的宽度：首页、项目、空间、个人设置四处共用一个数。拖过哪一处，到别处也是这个
// 宽度——各记各的话，从项目切到空间侧栏会跳一下。记在这台浏览器上，下次打开还是那样。

const KEY = 'cheesex.sidebarWidth'
export const SIDEBAR_MIN = 190
export const SIDEBAR_MAX = 480
export const SIDEBAR_DEFAULT = 280

const clamp = (w: number) => Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(w)))

function read(): number {
  const saved = Number(storageGet(KEY))
  return Number.isFinite(saved) && saved > 0 ? clamp(saved) : SIDEBAR_DEFAULT
}

const width = ref(read())

function setWidth(w: number) {
  width.value = clamp(w)
  // 存不进去（隐私模式、配额满）就只在这一次有效，拖出来的样子照旧。
  storageSet(KEY, String(width.value))
}

export function useSidebarWidth() {
  return { width: readonly(width), setWidth }
}
