// 提问接管输入框：只要有一组题还在等我回答，输入框那一格就画提问面板，不画
// composer。两者互斥（同一个 DOM 位置二选一），所以不需要点击就能接管——这正是
// Codex 桌面版的写法：`slot == null ? <输入框> : <提问面板>`。
//
// 关掉（Esc）只作用于这一组，记在 `dismissed` 里；「有 N 个问题待回答」那一条
// 可以一次性把全部收回。这保证 Esc 不会让问题永远消失——它只是先把面板收起来。
//
// 从 useChatPanel 里搬出来的：那段逻辑和房间的取数无关，只读 askGroups 和
// viewer，单独一件也就不会把那个文件顶过体积上限。
import type { AskGroupScope } from '../lib/askGroup'
import type { AskGroupState } from '../lib/askGroupState'

import { computed, reactive } from 'vue'

import { groupKey } from '../lib/askGroup'
import { canAnswer } from '../lib/askState'

export function useAskTakeover(options: { groups: Record<string, AskGroupState>; viewer: () => string }) {
  const dismissed = reactive(new Set<string>())
  function needsAnswer(state: AskGroupState): boolean {
    if (!state.data || state.data.settlement) return false
    return state.data.blocks.some((b) => canAnswer(b, options.viewer()) && !b.meta?.answer_log?.length)
  }
  // 多组同时待答时整组接管：取最新出现的那一组（useAskGroups 按插入序排列），
  // 其余仍留在 askReturn 里，收起后能连同一起回来，不会丢题。
  const askTakeover = computed<AskGroupState | null>(() => {
    const all = Object.values(options.groups)
    for (let i = all.length - 1; i >= 0; i--) {
      const state = all[i]!
      if (!dismissed.has(groupKey(state.scope)) && needsAnswer(state)) return state
    }
    return null
  })
  // 收起之后还剩多少道要回答的题（只数被收起的组，正在接管的那组不算）。
  const askReturn = computed(() => {
    let count = 0
    for (const state of Object.values(options.groups)) {
      if (!dismissed.has(groupKey(state.scope))) continue
      for (const b of state.data?.blocks ?? [])
        if (canAnswer(b, options.viewer()) && !b.meta?.answer_log?.length) count++
    }
    return count
  })
  return {
    askTakeover,
    askReturn,
    dismissAsk: (scope: AskGroupScope): void => void dismissed.add(groupKey(scope)),
    restoreAsk: (): void => dismissed.clear(),
  }
}
