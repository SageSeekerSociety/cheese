<script setup lang="ts">
// The picture for the device approval page: the code, the sign-in hint, the
// name field and the approved card. Approving is not here — the container
// `ConnectView.vue` reads the address, prefills the proposed name and calls the
// backend. This half receives props and emits `approve`.
import type { DeviceApproval } from '@/cx_types'

import { ref, watch } from 'vue'
import { I18nT } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  code: string
  loggedIn: boolean
  loading: boolean
  error: string | null
  approved: DeviceApproval | null
  /** The name the cli proposed (this machine's hostname), or '' while unknown. */
  proposedName: string
}>()

defineEmits<{
  approve: [name: string]
}>()

const loginLink = { name: 'SignIn' }
const deviceName = ref('')

// The container prefills the cli-proposed name once the lookup lands; keep the
// field in step with it so the human can just edit a real default.
watch(
  () => props.proposedName,
  (value) => {
    deviceName.value = value
  },
  { immediate: true }
)
</script>

<template>
  <div class="connect-page fill-height overflow-y-auto">
    <v-container class="py-8" style="max-width: 560px">
      <div class="mb-6">
        <!-- On phones the page title is in the top bar; do not repeat it here. -->
        <template v-if="$vuetify.display.mdAndUp">
          <div class="t-eyebrow mb-1">{{ t('project.connect.eyebrow') }}</div>
          <h1 class="t-page-title">{{ t('project.connect.title') }}</h1>
        </template>
        <div class="t-body c-muted mt-1">
          {{ t('project.connect.intro') }}
        </div>
      </div>

      <v-alert v-if="!code" type="warning" density="comfortable" class="mb-4">
        <i18n-t keypath="project.connect.noCode" tag="span">
          <template #command><code>cheesehost link connect</code></template>
        </i18n-t>
      </v-alert>

      <v-alert v-else-if="!loggedIn" type="info" density="comfortable" class="mb-4">
        {{ t('project.connect.signInFirst') }}
        <template #append>
          <BaseButton kind="primary" size="sm" :to="loginLink">{{ t('project.connect.signIn') }}</BaseButton>
        </template>
      </v-alert>

      <v-card v-else-if="!approved" class="pa-4">
        <div class="t-caption c-muted mb-1">{{ t('project.connect.code') }}</div>
        <div class="device-code mb-4">{{ code || '—' }}</div>

        <v-text-field
          v-model="deviceName"
          autocomplete="off"
          :label="t('project.connect.nameLabel')"
          :placeholder="t('project.connect.namePlaceholder')"
          variant="outlined"
          density="comfortable"
          hide-details
          class="mb-3"
          :disabled="loading"
          @keyup.enter="$emit('approve', deviceName)"
        />

        <v-alert v-if="error" type="error" density="compact" class="mb-3">
          {{ error }}
        </v-alert>

        <BaseButton kind="primary" :loading="loading" :disabled="!code" block @click="$emit('approve', deviceName)">
          {{ t('project.connect.approve') }}
        </BaseButton>
      </v-card>

      <v-card v-else class="pa-5 text-center">
        <v-icon size="40" color="success" class="mb-2"> mdi-check-circle-outline </v-icon>
        <h2 class="t-title mb-1">{{ t('project.connect.done') }}</h2>
        <div class="t-body c-muted mb-3">
          <i18n-t keypath="project.connect.bound" tag="span">
            <template #name
              ><strong>{{ approved.device_name }}</strong></template
            >
          </i18n-t>
        </div>
        <div class="t-caption c-muted mb-4">
          <i18n-t keypath="project.connect.finishing" tag="span">
            <template #command><code>cheesehost link connect</code></template>
          </i18n-t>
        </div>
        <BaseButton kind="secondary" :to="{ name: 'UserSettingsDevices' }">{{
          t('project.connect.viewDevices')
        }}</BaseButton>
      </v-card>
    </v-container>
  </div>
</template>

<style scoped>
.device-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  word-break: break-all;
  background: var(--fill);
  padding: 8px 10px;
  border-radius: 6px;
}
</style>
