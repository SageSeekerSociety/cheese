<script setup lang="ts">
import type { MyDevice } from '@/types/devices'

import { ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { t } from '@/i18n'

defineProps<{
  devices: MyDevice[]
  canManage: boolean
  loading: boolean
  loadFailed: boolean
  reason: string | null
  forbidden: boolean
  busy: string | null
  desktop: boolean
  command: string
  windowsCommand?: string
}>()
const emit = defineEmits<{
  load: []
  rename: [device: MyDevice, name: string]
  'remove-machine': [device: MyDevice]
  connect: []
}>()
const error = defineModel<string | null>('error', { required: true })
const adding = ref(false)
</script>

<template>
  <v-container class="px-6 py-5" fluid>
    <div class="compute-heading">
      <p class="t-body c-muted">{{ t('teams.compute.ownership') }}</p>
      <BaseButton v-if="canManage" kind="primary" prepend-icon="mdi-plus" @click="adding = !adding">{{
        t('teams.compute.addDevice')
      }}</BaseButton>
    </div>
    <v-alert v-if="error" type="error" closable class="mb-4" @click:close="error = null">{{ error }}</v-alert>
    <section v-if="adding && canManage" class="compute-add">
      <p class="t-body">{{ t('teams.compute.enrollHint') }}</p>
      <BaseButton v-if="desktop" kind="primary" @click="emit('connect')">{{
        t('teams.compute.connectHere')
      }}</BaseButton>
      <p class="t-caption c-muted">{{ t('teams.compute.cliHint') }}</p>
      <BaseButton kind="secondary" to="/download">{{ t('teams.compute.downloadDesktop') }}</BaseButton>
      <p class="t-caption c-muted">{{ t('account.devices.os.unix') }}</p>
      <pre>{{ command }}</pre>
      <template v-if="windowsCommand">
        <p class="t-caption c-muted">{{ t('account.devices.os.windows') }}</p>
        <pre>{{ windowsCommand }}</pre>
      </template>
    </section>
    <div v-if="loading" class="py-12 text-center"><v-progress-circular indeterminate /></div>
    <BaseLoadError
      v-else-if="loadFailed"
      :title="t('teams.compute.loadFailed')"
      :error="reason"
      :forbidden="forbidden"
      @retry="emit('load')"
    />
    <BaseEmptyState
      v-else-if="!devices.length"
      size="compact"
      icon="mdi-laptop-off"
      :title="t('teams.compute.selfHostedEmptyTitle')"
    />
    <div v-else class="compute-devices">
      <section v-for="device in devices" :key="device.device_id" class="compute-device">
        <div class="compute-heading">
          <v-text-field
            v-if="canManage"
            autocomplete="off"
            :model-value="device.name"
            :aria-label="t('account.thisDevice.name')"
            variant="outlined"
            density="compact"
            hide-details
            :disabled="busy === device.device_id"
            @change="emit('rename', device, ($event.target as HTMLInputElement).value)"
          />
          <h3 v-else class="t-title">{{ device.name }}</h3>
          <span class="compute-status" :class="{ 'compute-status--online': device.online }">{{
            device.online ? t('teams.compute.online') : t('teams.compute.offline')
          }}</span>
          <BaseButton
            v-if="canManage"
            kind="danger"
            size="sm"
            :loading="busy === device.device_id"
            @click="emit('remove-machine', device)"
            >{{ t('teams.compute.remove') }}</BaseButton
          >
        </div>
      </section>
    </div>
  </v-container>
</template>

<style scoped>
.compute-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 16px;
}
.compute-heading > :first-child {
  flex: 1;
  min-width: min(240px, 100%);
  margin: 0;
}
.compute-add {
  padding: 20px;
  margin-bottom: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.compute-add pre {
  padding: 12px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  background: var(--fill);
  font-size: 13px;
  line-height: var(--lh-13);
}
.compute-devices {
  display: grid;
  gap: 24px;
}
.compute-device {
  border-bottom: 1px solid var(--line);
  padding-bottom: 16px;
}
.compute-status {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.compute-status--online {
  color: var(--ok-ink);
}
</style>
