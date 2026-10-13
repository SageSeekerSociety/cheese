<script setup lang="ts">
// 成员自己的 Claude Code（#2991）：成员在自己电脑上登录的 Claude Code 跟着他进项目，
// 谁可以叫它由它的主人定：只有自己，或者项目里的人。它经由成员自己配置的账号把项目
// 内容发给模型，所以默认关闭，由管理者在这里打开，并可以只允许某几类连接。下面逐行列出
// 已经接入的、各用哪类连接；设备名和在线状态只在你自己那一行显示。
import type { OwnAgentCallers, OwnAgentConnection, OwnAgentKind } from '@/types/ownAgents'

import { computed, onMounted } from 'vue'

import { useOwnAgents } from '@/composables/useOwnAgents'
import { holdRevealGate } from '@/composables/useRevealGate'

import { t } from '@/i18n'
import { myHandle } from '@/me'
import { OWN_AGENT_KINDS } from '@/types/ownAgents'

defineOptions({ name: 'OwnAgentsSettings' })

const props = defineProps<{ projectId: string }>()

const { state, error, saving, load, setAllowed, setKinds, setCallers } = useOwnAgents(() => props.projectId)

const me = myHandle()
const callerItems = computed(() => [
  { title: t('work.projectSettings.ownAgents.callersOwner'), value: 'owner' },
  { title: t('work.projectSettings.ownAgents.callersProject'), value: 'project' },
])

function kindName(kind: OwnAgentKind): string {
  switch (kind) {
    case 'subscription':
      return t('work.projectSettings.ownAgents.kindSubscription')
    case 'api_key':
      return t('work.projectSettings.ownAgents.kindApiKey')
    default:
      return t('work.projectSettings.ownAgents.kindModelService')
  }
}

// 订阅档位按厂商的写法首字母大写（max → Max）；自定义模型服务带上模型名。
function connectionLabel(c: OwnAgentConnection): string {
  if (c.kind === 'subscription' && c.plan) {
    return t('work.projectSettings.ownAgents.kindSubscriptionPlan', {
      plan: c.plan.charAt(0).toUpperCase() + c.plan.slice(1),
    })
  }
  if (c.kind === 'model_service' && c.model) {
    return t('work.projectSettings.ownAgents.kindModelServiceModel', { model: c.model })
  }
  return kindName(c.kind)
}

const allowedKinds = computed(() => state.value?.kinds ?? [])

function toggleKind(kind: OwnAgentKind, on: boolean) {
  const next = OWN_AGENT_KINDS.filter((k) => (k === kind ? on : allowedKinds.value.includes(k)))
  if (next.length) void setKinds(next)
}

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
      <fieldset v-if="state.can_manage && state.allowed" class="own-agents__kinds" data-testid="own-agents-kinds">
        <legend class="t-body own-agents__kinds-label">{{ t('work.projectSettings.ownAgents.kindsLabel') }}</legend>
        <v-checkbox
          v-for="kind in OWN_AGENT_KINDS"
          :key="kind"
          :model-value="allowedKinds.includes(kind)"
          :label="kindName(kind)"
          :disabled="saving || (allowedKinds.length === 1 && allowedKinds.includes(kind))"
          density="compact"
          color="primary"
          hide-details
          @update:model-value="(on) => toggleKind(kind, !!on)"
        />
      </fieldset>
      <ul v-if="state.agents.length" class="own-agents__list">
        <li v-for="a in state.agents" :key="a.handle" class="own-agents__row">
          <span class="own-agents__name">{{ a.name }}</span>
          <span v-for="c in a.connections" :key="`${c.kind}:${c.plan}:${c.model}`" class="own-agents__machine">
            · {{ connectionLabel(c) }}
            <span v-if="!allowedKinds.includes(c.kind)" class="own-agents__refused">
              {{ t('work.projectSettings.ownAgents.kindNotAllowed') }}
            </span>
          </span>
          <span v-for="m in a.machines ?? []" :key="m.name" class="own-agents__machine">
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

.own-agents__refused {
  color: var(--danger-ink);
}

.own-agents__kinds {
  border: 0;
  margin: 16px 0 0;
  padding: 0;
}

.own-agents__kinds-label {
  margin-bottom: 4px;
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
