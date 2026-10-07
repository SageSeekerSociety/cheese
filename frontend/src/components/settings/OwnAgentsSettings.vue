<script setup lang="ts">
// 成员自己的 Claude Code（#2991）：成员在自己电脑上登录的 Claude Code 跟着他进项目，
// 只有他能叫。它经由成员自己的账号把项目内容发给模型，有保密要求的项目由管理者在这里
// 关掉。下面逐行列出已经接入的，和它们能跑在哪几台电脑上。
import { onMounted } from 'vue'

import { useOwnAgents } from '@/composables/useOwnAgents'
import { holdRevealGate } from '@/composables/useRevealGate'

import { t } from '@/i18n'

defineOptions({ name: 'OwnAgentsSettings' })

const props = defineProps<{ projectId: string }>()

const { state, error, saving, load, setAllowed } = useOwnAgents(() => props.projectId)

const releaseGate = holdRevealGate()
onMounted(() => load().finally(releaseGate))
</script>

<template>
  <div class="own-agents" data-testid="own-agents">
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
    <template v-if="state">
      <v-switch
        :model-value="state.allowed"
        :disabled="saving || !state.can_manage"
        :label="t('work.projectSettings.ownAgents.allow')"
        :hint="t('work.projectSettings.ownAgents.allowDetail')"
        persistent-hint
        color="primary"
        inset
        data-testid="own-agents-allowed"
        @update:model-value="(v) => setAllowed(Boolean(v))"
      />
      <ul v-if="state.agents.length" class="own-agents__list">
        <li v-for="a in state.agents" :key="a.handle" class="own-agents__row">
          <span class="own-agents__name">{{ a.name }}</span>
          <span v-for="m in a.machines" :key="m.name" class="own-agents__machine">
            · {{ m.name }}
            <span class="c-muted">
              {{ m.online ? t('work.projectSettings.ownAgents.online') : t('work.projectSettings.ownAgents.offline') }}
            </span>
          </span>
        </li>
      </ul>
      <p v-else class="t-body c-muted own-agents__empty">
        {{ t('work.projectSettings.ownAgents.empty') }}
      </p>
    </template>
  </div>
</template>

<style scoped>
.own-agents__list {
  list-style: none;
  margin: 16px 0 0;
  padding: 0;
}

.own-agents__row {
  padding: 8px 0;
  border-top: 1px solid var(--line);
  overflow-wrap: anywhere;
}

.own-agents__name {
  font-weight: 600;
}

.own-agents__empty {
  margin-top: 16px;
}
</style>
