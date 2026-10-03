// 帧里的 ESC 回到宿主时该做什么。
//
// 帧只报告「有人在里面按了 ESC」，处理方式是这一格的事，而且有先后：先收起开着的
// 标注条（它盖在预览上是此刻最上面的一层），没有再退全屏，都没有才把焦点从 iframe
// 挪回面板——焦点在帧里时宿主自己的键盘监听收不到按键，挪回来宿主的快捷键才重新
// 生效。和 CC 一样：ESC 是把控制权交回宿主，不是关掉整格。
import type { Ref } from 'vue'

export function usePreviewEscape(
  panel: Ref<HTMLElement | null>,
  fullscreen: Ref<boolean>,
  exitFullscreen: () => void,
  closeLocator: () => void,
  locatorOpen: () => boolean
): () => void {
  return () => {
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
