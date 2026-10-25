<script setup lang="ts">
// 预览站用：芝士面板，回答里的自动化卡片按 `states` 写状态（例子数据）。应用里这些状态由
// 外壳读回来（`useAgentRules`），这里直接给。
import type { PanelMessage } from '@/components/assistant/AssistantPanel.vue'
import type { AgentRuleNow } from '@/lib/agentRules'

import { provide } from 'vue'

import AssistantPanel from '@/components/assistant/AssistantPanel.vue'
import { agentRulesKey } from '@/lib/agentRules'

const props = defineProps<{
  states: Record<string, AgentRuleNow>
  messages: PanelMessage[]
  streaming: string | null
  tool: string | null
  notice: string | null
  busy: boolean
}>()

provide(agentRulesKey, {
  now: (id: string) => props.states[id] ?? null,
  act: async () => true,
})
</script>

<template>
  <AssistantPanel
    v-bind="$attrs"
    :messages="messages"
    :streaming="streaming"
    :tool="tool"
    :notice="notice"
    :busy="busy"
  />
</template>
