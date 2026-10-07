// 节点状态 (spec §9.1) 的取数：每一个这套部署能跑一轮的机器池，以及它此刻能不能跑。
//
// 取数和刷新留在这一层，不放进 `components/NodeBoard.vue`：`src/components` 下的组件
// 只从 props 画（guards 的 import-boundary），市场页（容器）调它、把结果递下去。
//
// 页面开着时每 15 秒轻刷一次，「在线」才一直是真的；只有第一次读（还没有任何结果）
// 算 loading，之后的刷新不让整块闪成转圈。卸载时停表。
//
// `enabled`：节点状态板不在默认页签上时，等它第一次为真（用户点开那个页签）才读、才起表；
// 之后不再看它，表一直走到卸载——和板子放在不设 eager 的 v-window-item 里时一样。
// 不给就挂上即读。
import type { MarketNodes } from '@/cx_types'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { getMarketNodes } from '@/api'
import { t } from '@/i18n'

export const MARKET_NODES_REFRESH_MS = 15000

export interface UseMarketNodesOptions {
  enabled?: () => boolean
}

export function useMarketNodes(options: UseMarketNodesOptions = {}) {
  const enabled = options.enabled ?? (() => true)
  const board = ref<MarketNodes | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let timer: number | undefined
  let mounted = false
  let started = false

  async function load() {
    loading.value = board.value === null
    error.value = null
    try {
      board.value = await getMarketNodes()
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.nodeBoard.loadFailed')
    } finally {
      loading.value = false
    }
  }

  function start() {
    if (started || !mounted || !enabled()) return
    started = true
    stopWatch()
    void load()
    timer = window.setInterval(() => void load(), MARKET_NODES_REFRESH_MS)
  }

  const stopWatch = watch(enabled, start)
  onMounted(() => {
    mounted = true
    start()
  })
  onBeforeUnmount(() => {
    mounted = false
    stopWatch()
    if (timer !== undefined) window.clearInterval(timer)
  })

  return { board, loading, error, load }
}
