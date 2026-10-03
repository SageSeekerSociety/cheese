<script setup lang="ts">
// 设备审批页 (P3 Phase B item 3): the frozen cli's device flow opens
// `<origin>/connect?code=…`; the signed-in human lands here and approves —
// which binds this machine to them as owner. A device is PURE COMPUTE (算力节点,
// execution-architecture v3) — approving does NOT mint an agent; which agent runs
// on it is decided per session/project later. Approval also deliberately does NOT
// ask which project the machine serves: a machine belongs to *you*, and which
// project uses its compute is decided later (device page / when a session picks it).
import type { DeviceApproval } from '../cx_types'

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { authToken, connectDevice, deviceProposedName } from '../api'
import { t } from '../i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const route = useRoute()
const code = computed(() => String(route.query.code ?? ''))
// Approving binds the machine to a real account, so a token is required. If the
// human landed here from the cli link without a live session, send them to log
// in; the router brings them straight back here afterwards.
const loggedIn = computed(() => !!authToken())
const loginLink = { name: 'SignIn' }

const loading = ref(false)
const error = ref<string | null>(null)
const approved = ref<DeviceApproval | null>(null)
// The compute-node name, prefilled with the name the cli proposed (this machine's
// hostname) so the human sees a real default and can just edit it — never "unnamed".
const deviceName = ref('')

// Prefill from the cli-proposed name (the device's hostname). Best-effort: if the
// lookup fails the field stays blank and the server still falls back to a real name.
onMounted(async () => {
  if (!code.value) return
  try {
    const r = await deviceProposedName(code.value)
    if (r.device_name) deviceName.value = r.device_name
  } catch {
    // leave blank; approval still names the node from the server-side default
  }
})

async function approve() {
  if (!code.value) {
    error.value = t('project.connect.missingCode')
    return
  }
  loading.value = true
  error.value = null
  try {
    approved.value = await connectDevice(code.value, deviceName.value.trim() || undefined)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('project.connect.failed')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="connect-page fill-height overflow-y-auto">
    <v-container class="py-8" style="max-width: 560px">
      <div class="mb-6">
        <!-- 手机上页名写在顶栏里，这里不再写一遍。 -->
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
          @keyup.enter="approve"
        />

        <v-alert v-if="error" type="error" density="compact" class="mb-3">
          {{ error }}
        </v-alert>

        <BaseButton kind="primary" :loading="loading" :disabled="!code" block @click="approve">
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
