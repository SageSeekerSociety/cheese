// 发布页、改题页上停在右边的芝士：收起来还是展开，记在这台浏览器里，下次打开照旧。
// 读不到存储（隐私模式、禁了站点数据）时照常展开，只是不记。
import { ref, watch } from 'vue'

const KEY = 'cheesex.agentDocked'

function read(): boolean {
  try {
    return localStorage.getItem(KEY) !== 'false'
  } catch {
    return true
  }
}

export function useAgentDock() {
  const docked = ref(read())
  watch(docked, (value) => {
    try {
      localStorage.setItem(KEY, String(value))
    } catch {
      // 记不住就算了：这一页里照样收起、展开。
    }
  })
  return docked
}
