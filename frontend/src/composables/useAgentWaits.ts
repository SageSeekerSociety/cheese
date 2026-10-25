// 这段对话里芝士正在等的事：用户让它「等某件事发生了再做」，它建了一条只做一次的规则，
// 事情发生时平台回到这段对话里接着说。面板在输入框上方列出来，可以取消。
// 一轮回答结束时重读（这一轮可能刚建了一条），换对话时也重读。
import type { Ref } from 'vue'
import type { AssistantRoutine } from '@/api'

import { computed, ref, watch } from 'vue'

import { getAssistantRoutines, setAssistantRoutine } from '@/api'

export interface AgentWait {
  id: string
  title: string
  when: string
}

export function useAgentWaits(conversation: Ref<string | null>, busy: Ref<boolean>) {
  const rules = ref<AssistantRoutine[]>([])
  let reading = 0

  async function load() {
    const mine = ++reading
    try {
      const { routines } = await getAssistantRoutines()
      if (mine === reading) rules.value = routines
    } catch {
      // Nothing listed is the same as nothing waited for; the next answer reads again.
    }
  }

  watch(conversation, (id) => id && void load(), { immediate: true })
  watch(busy, (now, before) => before && !now && void load())

  const waits = computed<AgentWait[]>(() =>
    rules.value
      .filter((r) => r.once && r.state === 'active' && r.conversationId === conversation.value)
      .map((r) => ({ id: r.id, title: r.title, when: r.when }))
  )

  /** Whether it was cancelled; when not, it is listed again. */
  async function cancel(id: string): Promise<boolean> {
    rules.value = rules.value.filter((r) => r.id !== id)
    try {
      rules.value = (await setAssistantRoutine(id, 'delete')).routines
      return true
    } catch {
      await load()
      return false
    }
  }

  return { waits, cancel }
}
