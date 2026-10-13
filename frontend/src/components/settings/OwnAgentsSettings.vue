<script setup lang="ts">
// 成员自己的 Claude Code（#2991）：成员在自己电脑上登录的 Claude Code 跟着他进项目，
// 谁可以叫它由它的主人定：只有自己，或者项目里的人。它经由成员自己配置的账号把项目
// 内容发给模型，有保密要求的项目由管理者在这里关掉。下面逐行列出已经接入的，和它们
// 能跑在哪几台电脑上。
import type { OwnAgentCallers } from '@/types/ownAgents'

import { computed, onMounted } from 'vue'

import { useOwnAgents } from '@/composables/useOwnAgents'
import { holdRevealGate } from '@/composables/useRevealGate'

import { t } from '@/i18n'
import { myHandle } from '@/me'

defineOptions({ name: 'OwnAgentsSettings' })

const props = defineProps<{ projectId: string }>()

const { state, error, saving, load, setAllowed, setCallers } = useOwnAgents(() => props.projectId)

const me = myHandle()
const callerItems = computed(() => [
  { title: t('work.projectSettings.ownAgents.callersOwner'), value: 'owner' },
  { title: t('work.projectSettings.ownAgents.callersProject'), value: 'project' },
])

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
      <p v-if="!state.can_manage" class="t-body c-muted own-agents__note">
        {{ t('work.projectSettings.ownAgents.readOnly') }}
      </p>
      <ul v-if="state.agents.length" class="own-agents__list">
        <li v-for="a in state.agents" :key="a.handle" class="own-agents__row">
          <span class="own-agents__name">{{ a.name }}</span>
          <span v-for="m in a.machines" :key="m.name" class="own-agents__machine">
            · {{ m.name }}
            <span class="c-muted">
              {{ m.online ? t('work.projectSettings.ownAgents.online') : t('work.projectSettings.ownAgents.offline') }}
            </span>
          </span>
          <template v-if="a.owner_handle === me">
            <v-select
              autocomplete="off"
              class="own-agents__callers"
              :model-value="a.callers"
              :items="callerItems"
              :label="t('work.projectSettings.ownAgents.callersLabel')"
              :disabled="saving"
              hide-details
              density="compact"
              data-testid="own-agent-callers"
              @update:model-value="(v: OwnAgentCallers) => setCallers(a.handle, v)"
            />
            <p class="t-meta c-muted own-agents__hint">{{ t('work.projectSettings.ownAgents.callersDetail') }}</p>
          </template>
          <span v-else class="c-muted own-agents__machine">
            ·
            {{
              a.callers === 'project'
                ? t('work.projectSettings.ownAgents.callersProjectTag')
                : t('work.projectSettings.ownAgents.callersOwnerTag')
            }}
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

.own-agents__callers {
  max-width: 240px;
  margin-top: 8px;
}

.own-agents__hint {
  margin-top: 4px;
}

.own-agents__note,
.own-agents__empty {
  margin-top: 16px;
}
</style>
