import { computed, type ComputedRef, readonly, ref } from 'vue'

import { useCompactDesktop } from './useWorkspaceLayout'

// 二级侧栏（项目的话题列表、空间/首页/后台那一栏）在桌面窄档（960–1180）里是一只
// 浮层：默认收起，靠 rail 顶上那颗开关打开，打开后压在正文上（不是把正文挤窄）。
// 比它宽就是常驻侧栏，收不起来；比 960 窄是手机外壳，走各自那套抽屉（`navigation`
// store 的 `isSecondaryDrawerOpen`），和这里无关。
//
// 「记着用户的选择」记在这台浏览器上：打开过一次，下次进来还是打开的。默认是收起，
// 所以窄档里第一次看到的是正文占满的那一屏。

const KEY = 'cheesex.sidebarExpanded'

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

// 用户的选择。多处（侧栏、rail 上的开关）读的是同一个 ref，所以两处看到的永远一致。
const expanded = ref(read())

export function useSidebarCollapse(): {
  compact: ComputedRef<boolean>
  expanded: Readonly<typeof expanded>
  /** 侧栏这会儿是不是开着：宽档里恒为 true，窄档里听用户的选择。 */
  open: ComputedRef<boolean>
  setExpanded: (next: boolean, persist?: boolean) => void
  toggle: () => void
  close: () => void
} {
  const compact = useCompactDesktop()
  const open = computed(() => !compact.value || expanded.value)

  function setExpanded(next: boolean, persist = true) {
    expanded.value = next
    // Esc 关掉的是这一次的浮层，不该把「我平时要它开着」这个选择也一起改掉。
    if (!persist) return
    try {
      localStorage.setItem(KEY, next ? '1' : '0')
    } catch {
      // 存不进去就只在这一次有效。
    }
  }

  return {
    compact,
    expanded: readonly(expanded),
    open,
    setExpanded,
    toggle: () => setExpanded(!expanded.value),
    close: () => setExpanded(false, false),
  }
}

// Esc 关上浮层之后，焦点回到打开它的那颗开关上——不然焦点落在已经藏起来的浮层里，
// 键盘用户下一步不知道自己在哪儿。开关上带这个属性，无论它挂在 rail 还是别处。
export function focusSidebarToggle(): void {
  const toggle = document.querySelector<HTMLElement>('[data-sidebar-toggle]')
  toggle?.focus()
}

/** rail 顶上那颗开关的状态属性（`aria-controls` 指向的那只抽屉的 id）。 */
export const SIDEBAR_DRAWER_ID = 'secondary-sidebar'
