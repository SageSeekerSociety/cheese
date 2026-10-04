// 一组 `role="tab"` 的键盘语义（WAI-ARIA Authoring Practices 的 Tabs 模式）：
//
// - roving tabindex：整组只有一格在 Tab 序列里（tabindex=0）——进来时是选中的那一格，
//   按方向键之后是焦点所在的那一格——按一次 Tab 进到这一组，再按一次就离开；
// - ←/→ 在格子之间移动焦点，Home / End 到头尾；Enter / 空格选中（手动激活）。不做自动
//   激活：有的页签一切就要取数（现场按队友、后台的状态筛选），有的会先问「放弃没发的
//   批注吗」，按一下方向键不该触发这些。
//
// 写成指令挂在 `role="tablist"` 那个元素上：各处页签的模板和点击逻辑不用改，只要多一个
// `v-roving-tabs`。选中哪一格仍由各自的 `aria-selected` 说了算，指令只读它。
import type { Directive } from 'vue'

function tabsOf(list: HTMLElement): HTMLElement[] {
  return Array.from(list.querySelectorAll<HTMLElement>('[role="tab"]')).filter(
    (tab) => tab.closest('[role="tablist"]') === list && !tab.hasAttribute('disabled')
  )
}

function setRoving(tabs: HTMLElement[], current: HTMLElement | undefined): void {
  for (const tab of tabs) tab.tabIndex = tab === current ? 0 : -1
}

/** 焦点不在这一组里时，Tab 序列里那一格回到选中的那一格。 */
function syncTabIndex(list: HTMLElement): void {
  const tabs = tabsOf(list)
  const focused = tabs.find((tab) => tab === document.activeElement)
  if (focused) return setRoving(tabs, focused)
  setRoving(tabs, tabs.find((tab) => tab.getAttribute('aria-selected') === 'true') ?? tabs[0])
}

function onFocusout(event: FocusEvent): void {
  const list = event.currentTarget as HTMLElement
  if (!list.contains(event.relatedTarget as Node | null)) syncTabIndex(list)
}

function onKeydown(event: KeyboardEvent): void {
  const list = event.currentTarget as HTMLElement
  const tabs = tabsOf(list)
  const from = tabs.indexOf(document.activeElement as HTMLElement)
  if (from < 0 || event.altKey || event.ctrlKey || event.metaKey) return
  const n = tabs.length
  let to: number
  switch (event.key) {
    case 'ArrowRight':
      to = (from + 1) % n
      break
    case 'ArrowLeft':
      to = (from - 1 + n) % n
      break
    case 'Home':
      to = 0
      break
    case 'End':
      to = n - 1
      break
    default:
      return
  }
  event.preventDefault()
  setRoving(tabs, tabs[to])
  tabs[to].focus()
}

export const vRovingTabs: Directive<HTMLElement> = {
  mounted(el) {
    syncTabIndex(el)
    el.addEventListener('keydown', onKeydown)
    el.addEventListener('focusout', onFocusout)
  },
  updated(el) {
    syncTabIndex(el)
  },
  unmounted(el) {
    el.removeEventListener('keydown', onKeydown)
    el.removeEventListener('focusout', onFocusout)
  },
}
