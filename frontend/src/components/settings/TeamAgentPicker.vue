<script setup lang="ts">
import type { OwnAgentsSettings } from '@/types/ownAgents'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{ state: OwnAgentsSettings | null; error: string; saving: boolean }>()
const emit = defineEmits<{ add: [connectionId: string, model: string, deviceId: string | null] }>()
const connectionId = ref('')
const model = ref('')
const environmentId = ref('')
const environments = computed(() => [
  ...(props.state?.standard_sandbox_available
    ? [{ id: 'sandbox:standard', name: t('work.projectSettings.ownAgents.standardSandbox'), deviceId: null }]
    : []),
  ...(props.state?.devices ?? []).map((device) => ({
    id: `device:${device.device_id}`,
    name: device.name,
    deviceId: device.device_id,
  })),
])
const environment = computed(() => environments.value.find((item) => item.id === environmentId.value))
watch(
  environments,
  (items) => {
    if (!items.some((item) => item.id === environmentId.value)) {
      environmentId.value = items.find((item) => item.deviceId === null)?.id ?? ''
    }
  },
  { immediate: true }
)
const choices = computed(() =>
  (props.state?.my_connections ?? [])
    .filter((c) => props.state?.can_manage || c.owner_user_id != null)
    .map((c) => ({
      ...c,
      label:
        c.kind === 'platform'
          ? t('account.modelConnections.platformTitle')
          : c.name ||
            c.service_host ||
            (c.kind === 'subscription'
              ? t('account.modelConnections.subscription', { plan: c.plan ?? '' })
              : t('account.modelConnections.apiKey')),
    }))
)
const connection = computed(() => choices.value.find((c) => c.id === connectionId.value))
watch(connectionId, () => {
  model.value = connection.value?.allowed_models[0] ?? ''
})
const canCreate = computed(() => !!connection.value?.allowed_models.includes(model.value) && !!environment.value)
function create() {
  if (canCreate.value && environment.value) emit('add', connectionId.value, model.value, environment.value.deviceId)
}
</script>

<template>
  <div data-testid="own-agents">
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
    <p class="t-body c-muted mb-4">{{ t('work.projectSettings.ownAgents.intro') }}</p>
    <div v-if="state && (state.can_manage || choices.length)" class="team-agent-picker">
      <v-select
        v-model="connectionId"
        autocomplete="off"
        :items="choices"
        item-title="label"
        item-value="id"
        :label="t('work.projectSettings.ownAgents.connection')"
      />
      <v-select
        v-model="model"
        autocomplete="off"
        :items="connection?.allowed_models ?? []"
        :label="t('work.projectSettings.ownAgents.model')"
      />
      <v-select
        v-model="environmentId"
        autocomplete="off"
        :items="environments"
        item-title="name"
        item-value="id"
        :label="t('work.projectSettings.ownAgents.environment')"
        :no-data-text="t('work.projectSettings.ownAgents.noEnvironments')"
      />
      <p v-if="!state.standard_sandbox_available" class="t-body c-muted" role="status">
        {{ t('work.projectSettings.ownAgents.sandboxUnavailable') }}
      </p>
      <p v-else-if="environment?.deviceId === null" class="t-body c-muted">
        {{ t('work.projectSettings.ownAgents.sandboxAbout') }}
      </p>
      <BaseButton kind="primary" :disabled="!canCreate" :loading="saving" @click="create">{{
        t('work.projectSettings.ownAgents.add')
      }}</BaseButton>
    </div>
  </div>
</template>

<style scoped>
.team-agent-picker {
  display: grid;
  gap: 16px;
  padding-top: 8px;
}
</style>
