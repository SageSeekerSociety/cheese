// 发布页、改题页上停在右边的芝士：收起来还是展开，记在这台浏览器里，下次打开照旧。
// 读不到存储（隐私模式、禁了站点数据）时照常展开，只是不记。
import { ref, watch } from 'vue'

import { storageGet, storageSet } from '@/lib/browserStorage'

const KEY = 'cheesex.agentDocked'

export function useAgentDock() {
  const docked = ref(storageGet(KEY) !== 'false')
  watch(docked, (value) => void storageSet(KEY, String(value)))
  return docked
}
