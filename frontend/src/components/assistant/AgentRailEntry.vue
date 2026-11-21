<template>
  <!-- The way into the person's own assistant from anywhere on desktop: its face, in the rail above
       the person's avatar. Hidden where no personal 芝士 answers (inside a project). -->
  <button
    v-if="agentAvailable"
    type="button"
    class="agent-rail"
    :class="{ 'agent-rail--on': agentOpen }"
    :aria-label="label"
    :aria-pressed="agentOpen"
    data-testid="agent-entry"
    @click="toggleAgent"
  >
    <v-tooltip activator="parent" location="end" :text="label" />
    <CheeseAvatar :size="28" :state="agentThread.busy.value ? 'think' : null" />
  </button>
</template>

<script setup lang="ts">
// 桌面左栏上的芝士：头像那一颗，点了打开或收起全站那个面板（layouts/AgentHost.vue）。
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import { t } from '@/i18n'
import { agentAvailable, agentOpen, agentThread, toggleAgent } from '@/stores/agent'

const label = t('tasks.assistant.ask')
</script>

<style scoped>
.agent-rail {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  margin: 0 auto 8px;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.agent-rail:hover,
.agent-rail--on {
  background: var(--fill);
}

.agent-rail:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
