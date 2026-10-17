// 队列页的**键盘**那一半（§8）：`j/k/H/U/A/M` 与分诊 `1/2/3`、`/` 与 `Esc` 的逐层后退。
//
// 这些键挂在 window 上，挪光标、分诊、撤销、已读、指派各自的动作都在别的模块里，这里
// 只负责认键、判断该不该接手，再把动作分派出去。拆出来的理由见 `../useAdminQueue.ts`
// 的文件头。
import type { ComputedRef, Ref } from 'vue'
import type { FeedbackCard, FeedbackStatus } from '@/cx_types'
import type { AdminQueueDeps, AdminQueueStore } from './types'

/** 分诊键落在梯子的第几格（§8 表里的 `1` 进行中 / `2` 已解决 / `3` 已上线）。存的是
 *  **下标**而不是状态名：梯子由服务端给（`store.statusLadder`），下标才是那三条键的
 *  意思，名字会跟着服务端变。 */
const TRIAGE_SLOTS: Record<string, number> = { 1: 1, 2: 2, 3: 3 }

/** 这一份要的：`store`、页面报回来的搜索焦点回调、两个开关、当前行与撤销条，以及挪
 *  光标、撤销、已读、指派、推状态这几个动作（各自的主人在别的模块里）。 */
export interface AdminQueueKeyboardDeps {
  store: AdminQueueStore
  pageDeps: AdminQueueDeps
  detailOpen: Ref<boolean>
  triageOpen: Ref<boolean>
  current: ComputedRef<FeedbackCard | null>
  undo: Ref<{ id: string; from: FeedbackStatus; message: string } | null>
  step: (delta: number, open?: boolean) => void
  undoTriage: () => void
  markCurrentRead: () => void
  focusAssignee: () => void | Promise<void>
  writeStatus: (id: string, to: FeedbackStatus, byKey: boolean) => Promise<void>
}

/** 认键与分派：window 上的 `keydown` 处理，和 `Esc` 的逐层后退。 */
export function useAdminQueueKeyboard(deps: AdminQueueKeyboardDeps) {
  const { store, pageDeps, detailOpen, triageOpen, current, undo } = deps

  function isTyping(target: EventTarget | null): boolean {
    const el = target as HTMLElement | null
    if (!el || !el.tagName) return false
    return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable
  }

  /** 焦点是不是落在**某一个自己收方向键的格子**里。
   *
   *  两个视图各有一套 `j`/`k` 处理（`AdminQueueList` / `AdminFeedbackTable`，roving
   *  tabindex 归它们管），页面这一层只在格子已经不在 DOM 里的时候接手 —— 宽屏打开
   *  详情时整块被 `v-if` 换掉，就是那一种。
   *
   *  认的是一个**两个格子都标了的属性**，不是类名。以前这里写的是 `.qlist`，于是
   *  表格视图（根类名是 `.aft`）整个漏在外面：窄屏打开抽屉、焦点在表格里时，表格的
   *  `@keydown` 和 window 上这一个同时命中，一次 `j` 前进两行、光标越过一行。两个
   *  视图写了两套类名，守卫只认了一套 —— 属性说的是「这一片子树自己收方向键」，
   *  新增第三个视图时标上它就有同样的待遇，改类名也不会再悄悄把守卫弄丢。 */
  function gridOwnsArrows(): boolean {
    return !!document.activeElement?.closest('[data-owns-arrow-keys]')
  }

  /** `Esc` 逐层后退（§8）：一次只退一层，先分诊面板、再详情。 */
  function onEscape() {
    if (!detailOpen.value) return
    if (triageOpen.value) {
      triageOpen.value = false
      return
    }
    detailOpen.value = false
  }

  /** 队列页这一层的键（§8 的「列表 / 详情」两档）：
   *
   *  - 分诊那一批（`1/2/3/H/U/A/M`）**不依赖抽屉开着**（F-12）—— 窄屏上键盘用户不该
   *    被困在抽屉里，所以它们挂在 window 上，焦点在哪儿都算。
   *  - `j`/`k` 在列表自己手里（roving tabindex），这里**只在详情打开、列表已经不在
   *    DOM 里的时候**接手：宽屏接管之后连按 `j` 能一条条往下看，正是 F-06 防抖存在的
   *    理由。列表还在时两边都收一次键，会一次跳两行。
   */
  function onKeydown(event: KeyboardEvent) {
    if (event.metaKey || event.ctrlKey || event.altKey) return

    if (isTyping(event.target)) {
      // 输入框里的 `Esc` 先退焦点：不然在搜索框里按 `Esc` 会直接把详情关掉，而人的意思是
      // 「我不打了」。
      if (event.key === 'Escape') (event.target as HTMLElement).blur()
      return
    }

    if (event.key === '/') {
      event.preventDefault()
      pageDeps.focusSearch()
      return
    }
    if (event.key === 'Escape') {
      onEscape()
      return
    }

    const detailHoldsFocus = detailOpen.value && !gridOwnsArrows()

    if (event.key === 'j' || event.key === 'ArrowDown') {
      if (!detailHoldsFocus) return
      event.preventDefault()
      deps.step(1)
      return
    }
    if (event.key === 'k' || event.key === 'ArrowUp') {
      if (!detailHoldsFocus) return
      event.preventDefault()
      deps.step(-1)
      return
    }

    if (event.key === 'h' || event.key === 'H') {
      // 「零步数」：状态不动，只往前挪一行 —— 扫一遍队列时不必每按一次都改点什么。
      if (!current.value) return
      event.preventDefault()
      deps.step(1)
      return
    }
    if (event.key === 'u' || event.key === 'U') {
      if (!undo.value) return
      event.preventDefault()
      deps.undoTriage()
      return
    }
    if (event.key === 'm' || event.key === 'M') {
      deps.markCurrentRead()
      return
    }
    if (event.key === 'a' || event.key === 'A') {
      void deps.focusAssignee()
      return
    }

    const slot = TRIAGE_SLOTS[event.key]
    if (slot !== undefined) {
      const to = store.statusLadder[slot]
      if (!to || !current.value) return
      event.preventDefault()
      void deps.writeStatus(current.value.id, to, true)
    }
  }

  return { onKeydown }
}
