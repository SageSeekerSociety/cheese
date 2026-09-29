// 命令面板开没开着。整个应用只有一个面板，入口有好几个（⌘K、话题列表顶栏的搜索
// 图标），所以开关放在模块里，谁都能开。
import { ref } from 'vue'

export const paletteOpen = ref(false)

export function openPalette() {
  paletteOpen.value = true
}
