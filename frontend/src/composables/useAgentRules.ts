// 芝士建的自动化现在怎样：回答里那张卡片按它写「等待中」「已开始」……，卡片上的取消、暂停、
// 恢复也走这里。卡片画在很深的回答里，状态和动作由外壳提供（`provide`），卡片自己不取数。
// 打开一段对话时读；一轮回答结束时重读（这一轮可能刚建了一条），对话在别处有了新动静时也重读
// （等的事到了）。
import type { Ref } from 'vue'
import type { AssistantRoutine } from '@/api'
import type { AgentRuleNow } from '@/lib/agentRules'

import { ref, watch } from 'vue'

import { getAssistantRoutines, setAssistantRoutine } from '@/api'
import { nowOf } from '@/lib/agentRules'

export function useAgentRules(conversation: Ref<string | null>, busy: Ref<boolean>) {
  const listed = ref<AssistantRoutine[] | null>(null)
  const cancelled = ref(new Set<string>())
  let reading = 0

  async function load() {
    const mine = ++reading
    try {
      const { routines } = await getAssistantRoutines()
      if (mine === reading) listed.value = routines
    } catch {
      // Unread, the cards say nothing of their state; the next answer reads again.
    }
  }

  // Read once a conversation is open, which is where the cards are; not on every page load.
  watch(conversation, (id) => id && void load(), { immediate: true })
  watch(busy, (now, before) => before && !now && void load())

  function now(id: string): AgentRuleNow | null {
    if (cancelled.value.has(id)) return { state: 'cancelled' }
    if (!listed.value) return null
    return nowOf(listed.value.find((r) => r.id === id))
  }

  async function act(id: string, action: 'cancel' | 'pause' | 'resume'): Promise<boolean> {
    try {
      listed.value = (await setAssistantRoutine(id, action === 'cancel' ? 'delete' : action)).routines
    } catch {
      return false
    }
    if (action === 'cancel') cancelled.value = new Set([...cancelled.value, id])
    return true
  }

  return { now, act, load }
}
