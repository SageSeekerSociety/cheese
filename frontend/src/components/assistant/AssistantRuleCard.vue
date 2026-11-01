<script setup lang="ts">
// 芝士在这一轮建或改的一条自动化：什么时候、做什么，底下一行是它现在怎样，和能做的那一件
// （等着的可以取消，开着的可以暂停，暂停的可以恢复）。状态和动作由外壳提供（`agentRulesKey`），
// 没有提供时只画卡片本身。
import type { AgentRuleNow } from '@/lib/agentRules'
import type { AssistantRule } from '@/stores/agentThread'

import { computed, inject, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { agentRulesKey } from '@/lib/agentRules'
import { toast } from '@/lib/toast'

const props = defineProps<{ rule: AssistantRule }>()

const rules = inject(agentRulesKey, null)
const now = computed<AgentRuleNow | null>(() => rules?.now(props.rule.id) ?? null)
const acting = ref(false)

const said = computed(() => {
  const n = now.value
  if (!n) return ''
  if (n.state === 'paused') return t(`tasks.assistant.rule.paused.${n.why}`)
  return t(`tasks.assistant.rule.${n.state}`, { days: 14 })
})
const action = computed(() => {
  const state = now.value?.state
  if (state === 'waiting') return 'cancel' as const
  if (state === 'on') return 'pause' as const
  if (state === 'paused') return 'resume' as const
  return null
})
const over = computed(() => ['gaveUp', 'cancelled', 'ended'].includes(now.value?.state ?? ''))

async function run() {
  if (!rules || !action.value || acting.value) return
  acting.value = true
  const done = await rules.act(props.rule.id, action.value)
  acting.value = false
  if (!done) toast.error(t('tasks.assistant.rule.failed'))
}
</script>

<template>
  <section class="arc" :class="{ 'arc--over': over }">
    <div class="arc__body">
      <span class="arc__when" data-user-content>{{ rule.when }}</span>
      <span class="arc__note" data-user-content>{{ rule.note }}</span>
    </div>
    <footer v-if="said" class="arc__foot">
      <span class="arc__state">{{ said }}</span>
      <BaseButton v-if="action" kind="ghost" size="sm" :disabled="acting" @click="run">
        {{ t(`tasks.assistant.rule.${action}`) }}
      </BaseButton>
    </footer>
  </section>
</template>

<style scoped>
.arc {
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}
.arc--over {
  border-color: var(--line);
}
.arc__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 12px;
}
.arc__when {
  font-weight: 600;
  color: var(--ink);
}
.arc--over .arc__when {
  color: var(--muted);
}
.arc__note {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.arc__foot {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 36px;
  padding: 0 8px 0 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  border-top: 1px solid var(--line);
}
.arc__state {
  flex: 1;
}
</style>
