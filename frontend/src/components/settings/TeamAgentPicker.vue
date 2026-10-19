<script setup lang="ts">
import type { OwnAgentsSettings } from '@/types/ownAgents'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{ state: OwnAgentsSettings | null; error: string; saving: boolean }>()
const emit = defineEmits<{ add: [connectionId: string, model: string, deviceId: string] }>()
const connectionId = ref('')
const model = ref('')
const deviceId = ref('')
const choices = computed(() =>
  (props.state?.my_connections ?? []).map((c) => ({
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
function create() {
  if (connectionId.value && model.value && deviceId.value) emit('add', connectionId.value, model.value, deviceId.value)
}
</script>

<template>
  <div data-testid="own-agents">
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
    <p class="t-body c-muted mb-4">{{ t('work.projectSettings.ownAgents.intro') }}</p>
    <div v-if="state?.can_manage" class="team-agent-picker">
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
        v-model="deviceId"
        autocomplete="off"
        :items="state.devices"
        item-title="name"
        item-value="device_id"
        :label="t('work.projectSettings.ownAgents.device')"
      />
      <BaseButton kind="primary" :disabled="!connectionId || !model || !deviceId" :loading="saving" @click="create">{{
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
