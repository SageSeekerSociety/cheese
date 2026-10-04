// 帧里的 ESC 回到宿主时该做什么。
//
// 帧只报告「有人在里面按了 ESC」，处理方式是这一格的事，而且有先后：先退出圈选（鼠标
// 正被收着，这一层空着键盘的圈选状态最上面），再收起开着的标注条（它盖在预览上是此刻
// 最上面的一层），没有再退全屏，都没有才把焦点从 iframe 挪回面板——焦点在帧里时宿主
// 自己的键盘监听收不到按键，挪回来宿主的快捷键才重新生效。和 CC 一样：ESC 是把控制权
// 交回宿主，不是关掉整格。
import type { Ref } from 'vue'

/** 圈选开着时 ESC 先关它（开关在 usePreviewPick）。 */
interface PickSwitch {
  on: Ref<boolean>
  set: (on: boolean) => void
}

export function usePreviewEscape(
  panel: Ref<HTMLElement | null>,
  fullscreen: Ref<boolean>,
  exitFullscreen: () => void,
  closeLocator: () => void,
  locatorOpen: () => boolean,
  pick?: PickSwitch
): () => void {
  return () => {
    if (pick?.on.value) {
      pick.set(false)
      return
    }
    if (locatorOpen()) {
      closeLocator()
      return
    }
    if (fullscreen.value) {
      exitFullscreen()
      return
    }
    panel.value?.focus()
  }
}
