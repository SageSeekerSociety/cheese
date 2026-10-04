// 快捷键表的数据形状，和主应用那张表的开关（`?`、输入框旁的键盘按钮都开它）。
import { ref } from 'vue'

export interface ShortcutRow {
  keys: string[]
  /** 序列键（`G` 然后 `Q`）与二选一（`↓` / `↑`）在表里长得像，读起来是两回事。 */
  sequence?: boolean
  action: string
  note?: string
}

export interface ShortcutGroup {
  scope: string
  rows: ShortcutRow[]
}

/** 主应用的快捷键表开着没有。 */
export const appShortcutSheetOpen = ref(false)
