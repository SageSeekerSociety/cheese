// Alt+Shift+C（Mac 上 ⌥⇧C）：从房间里任何地方回到输入框——同 Microsoft Teams 的
// 「转到撰写框」。带修饰键，所以焦点在别的输入框里也认；不带 ⌘/Ctrl，不和浏览器抢。
//
// 一页上可能同时开着两栏（频道和它的一条支线），各有一个输入框：去焦点此刻所在的那
// 一栏的；焦点不在哪一栏里时，去先挂上的那一栏（频道）。每一栏都登记同一条命令，
// 谁被按到都按这个规则挑，所以先登记的是哪一栏无所谓。
import type { Ref } from 'vue'

import { onBeforeUnmount } from 'vue'

import { useCommands } from '@/commands'
import { t } from '@/i18n'

interface Seat {
  /** 这一栏的根元素：焦点在它里面，就是这一栏。 */
  root: Ref<HTMLElement | null>
  /** 此刻有能打字的输入框（没关、轮得到我说话）。 */
  open: () => boolean
  focus: () => void
}

const seats: Seat[] = []

/** 按下去该去哪一栏。被 keep-alive 收起来的那一栏不在文档里，不算。焦点在一个开着的
 *  浮层（搜索面板、对话框、菜单）里时哪一栏都不去：把焦点拉到它背后的输入框，浮层还
 *  开着、Esc 也关不掉它了。 */
function seatToFocus(): Seat | undefined {
  const active = document.activeElement
  if (active?.closest('.v-overlay--active')) return undefined
  const live = seats.filter((seat) => seat.open() && seat.root.value?.isConnected)
  return live.find((seat) => active && seat.root.value?.contains(active)) ?? live[0]
}

export function useComposerShortcut(seat: Seat) {
  seats.push(seat)
  onBeforeUnmount(() => seats.splice(seats.indexOf(seat), 1))
  useCommands(() =>
    seat.open()
      ? [
          {
            id: 'chat.focusComposer',
            title: t('global.shortcuts.focusComposer'),
            shortcut: 'alt+shift+c',
            // 命令面板一打开，焦点就在面板里了：「转到输入框」在那里没有意义。
            palette: false as const,
            run: () => seatToFocus()?.focus(),
          },
        ]
      : []
  )
}
