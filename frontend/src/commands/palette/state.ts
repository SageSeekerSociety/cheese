// 命令面板开没开着。整个应用只有一个面板，入口有好几个（⌘K、话题列表顶栏的搜索
// 图标），所以开关放在模块里，谁都能开。
import { ref } from 'vue'

export const paletteOpen = ref(false)

export function openPalette() {
  paletteOpen.value = true
}

/**
 * 面板在问一句话（在面板里重命名一个话题）：输入框换成这一句的答案，回车交给
 * submit，Esc 回到结果列表。
 */
export interface PaletteAsk {
  /** 输入框左边那一小块：在做什么。 */
  title: string
  placeholder: string
  value: string
  submit: (value: string) => void
}

export const paletteAsk = ref<PaletteAsk | null>(null)
