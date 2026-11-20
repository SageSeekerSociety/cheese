import { computed, type ComputedRef, readonly, type Ref, ref } from 'vue'

import { SIDEBAR_FOLD_KEY } from './sidebarWidth'
import { useCompactDesktop } from './useWorkspaceLayout'

import { rememberedFold, rememberFold } from '@/components/layout-area'
import { storageGet, storageSet } from '@/lib/browserStorage'

// 二级侧栏（项目的话题列表、空间/首页/后台那一栏）在桌面窄档（960–1180）里是一只
// 浮层：默认收起，靠顶栏最左那颗开关打开，打开后压在正文上（不是把正文挤窄）。
// 比它宽是常驻侧栏，同一颗开关把它整个收起（`useSidebarToggle`）；比 960 窄是手机外壳，
// 走各自那套抽屉（`navigation` store 的 `isSecondaryDrawerOpen`），和这里无关。
//
// 「记着用户的选择」记在这台浏览器上：打开过一次，下次进来还是打开的。默认是收起，
// 所以窄档里第一次看到的是正文占满的那一屏。

const KEY = 'cheesex.sidebarExpanded'

function read(): boolean {
  return storageGet(KEY) === '1'
}

// 用户的选择。多处（侧栏、顶栏上的开关）读的是同一个 ref，所以两处看到的永远一致。
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
    // 存不进去就只在这一次有效。
    storageSet(KEY, next ? '1' : '0')
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

/**
 * 顶栏最左那颗开关：宽档里把常驻侧栏整个收起、再放出来（记在 `cheesex.sidebarFolded`，所有
 * 页面同一个状态），窄档里开合浮层（记在 `cheesex.sidebarExpanded`）。两档一颗开关，各记各的。
 */
export function useSidebarToggle(): {
  /** 侧栏此刻看得见吗。 */
  shown: ComputedRef<boolean>
  /** 宽档里收起了没有（外壳据此把侧栏那一格藏起来）。 */
  folded: Readonly<Ref<boolean>>
  toggle: () => void
} {
  const { compact, open, toggle: toggleDrawer } = useSidebarCollapse()
  const folded = rememberedFold(SIDEBAR_FOLD_KEY)
  const shown = computed(() => (compact.value ? open.value : !folded.value))
  function toggle() {
    if (compact.value) toggleDrawer()
    else rememberFold(SIDEBAR_FOLD_KEY, !folded.value)
  }
  return { shown, folded, toggle }
}

// Esc 关上浮层之后，焦点回到打开它的那颗开关上——不然焦点落在已经藏起来的浮层里，
// 键盘用户下一步不知道自己在哪儿。开关上带这个属性。
export function focusSidebarToggle(): void {
  const toggle = document.querySelector<HTMLElement>('[data-sidebar-toggle]')
  toggle?.focus()
}

/** 顶栏那颗开关的 `aria-controls` 指向的那只抽屉的 id。 */
export const SIDEBAR_DRAWER_ID = 'secondary-sidebar'
