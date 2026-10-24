<script setup lang="ts">
import type { MyDevice } from '@/types/devices'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'
import { lastSeenOf } from '@/types/devices'

const props = defineProps<{
  devices: MyDevice[]
  /** 自己名下的设备（个人设置里）：开头那句说的是你自己的。 */
  personal?: boolean
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

/** 离线的设备写上次在线的时间；在线的只说在线。 */
function status(device: MyDevice): string {
  if (device.online) return t('teams.compute.online')
  const seen = relTime(lastSeenOf(device))
  return seen ? t('teams.compute.offlineSince', { time: seen }) : t('teams.compute.offline')
}

// 正在改名的那台设备和输入框里的字。保存后等这一次请求回来：没出错就关上，出错留着框和字。
const renaming = ref<MyDevice | null>(null)
const renameText = ref('')
const renameReady = computed(() => {
  const name = renameText.value.trim()
  return name !== '' && name !== renaming.value?.name
})
function openRename(device: MyDevice) {
  // The dialog shows the page's error as this rename's own; one left from another action is not.
  error.value = null
  renameText.value = device.name
  renaming.value = device
}
function saveRename() {
  if (!renaming.value || !renameReady.value || props.busy === renaming.value.device_id) return
  emit('rename', renaming.value, renameText.value.trim())
}
watch(
  () => props.busy,
  (now, before) => {
    if (renaming.value && before === renaming.value.device_id && now === null && !error.value) renaming.value = null
  }
)
</script>

<template>
  <div class="settings-page">
    <header class="settings-page__head">
      <div>
        <h1 class="t-page-title">{{ t('home.nav.teamCompute') }}</h1>
        <p class="settings-page__lede">
          {{ personal ? t('teams.compute.ownershipPersonal') : t('teams.compute.ownership') }}
        </p>
      </div>
      <BaseButton v-if="canManage" kind="primary" prepend-icon="mdi-plus" @click="adding = !adding">{{
        t('teams.compute.addDevice')
      }}</BaseButton>
    </header>
    <v-alert v-if="error" type="error" closable @click:close="error = null">{{ error }}</v-alert>
    <section v-if="adding && canManage" class="settings-card compute-add">
      <p class="t-body">{{ t('teams.compute.enrollHint') }}</p>
      <BaseButton v-if="desktop" kind="primary" class="compute-add__btn" @click="emit('connect')">{{
        t('teams.compute.connectHere')
      }}</BaseButton>
      <p class="compute-add__note">{{ t('teams.compute.cliHint') }}</p>
      <BaseButton kind="secondary" class="compute-add__btn" to="/download">{{
        t('teams.compute.downloadDesktop')
      }}</BaseButton>
      <p class="compute-add__note">{{ t('account.devices.os.unix') }}</p>
      <pre>{{ command }}</pre>
      <template v-if="windowsCommand">
        <p class="compute-add__note">{{ t('account.devices.os.windows') }}</p>
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
    <ul v-else class="settings-card compute-list">
      <li v-for="device in devices" :key="device.device_id" class="compute-row">
        <v-icon class="compute-row__icon" icon="mdi-laptop" size="20" aria-hidden="true" />
        <div class="compute-row__main">
          <div class="compute-row__name" data-user-content>{{ device.name }}</div>
          <div class="compute-row__status">
            <span class="compute-dot" :class="{ 'compute-dot--on': device.online }" aria-hidden="true" />
            <span :class="{ 'compute-row__online': device.online }">{{ status(device) }}</span>
          </div>
        </div>
        <div v-if="canManage" class="compute-row__actions">
          <BaseButton kind="ghost" size="sm" :disabled="busy === device.device_id" @click="openRename(device)">{{
            t('teams.compute.rename')
          }}</BaseButton>
          <BaseButton
            kind="ghost"
            size="sm"
            :loading="busy === device.device_id && !renaming"
            :disabled="busy === device.device_id"
            @click="emit('remove-machine', device)"
            >{{ t('teams.compute.remove') }}</BaseButton
          >
        </div>
      </li>
    </ul>

    <AdaptiveDialog
      :model-value="!!renaming"
      :title="t('teams.compute.renameTitle')"
      :primary-label="t('global.save')"
      :primary-loading="!!renaming && busy === renaming.device_id"
      :primary-disabled="!renameReady"
      @update:model-value="renaming = null"
      @primary="saveRename"
    >
      <v-text-field
        v-if="renaming"
        v-model="renameText"
        autocomplete="off"
        :label="t('account.thisDevice.name')"
        @keydown.enter.prevent="saveRename"
      />
      <p v-if="error" role="alert" class="compute-rename__error">{{ error }}</p>
    </AdaptiveDialog>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.compute-add {
  padding: 20px 24px;
}

.compute-add > * + * {
  margin-top: 12px;
}

.compute-add__note {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.compute-add pre {
  padding: 12px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  background: var(--fill);
  border-radius: var(--radius-md);
  font-size: 13px;
  line-height: var(--lh-13);
}

.compute-rename__error {
  margin-top: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger-ink);
}

.compute-list {
  padding: 0;
  margin: 0;
  list-style: none;
}

.compute-row {
  display: flex;
  gap: 12px;
  align-items: center;
  min-height: 64px;
  padding: 12px 16px 12px 24px;
}

.compute-row + .compute-row {
  border-top: 1px solid var(--line);
}

.compute-row__icon {
  flex-shrink: 0;
  color: var(--muted);
}

.compute-row__main {
  flex: 1;
  min-width: 0;
}

.compute-row__name {
  overflow: hidden;
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compute-row__status {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: 2px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.compute-row__online {
  color: var(--ok-ink);
}

.compute-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  background: var(--faint);
  border-radius: var(--radius-pill);
}

.compute-dot--on {
  background: var(--ok);
}

.compute-row__actions {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
}

@media (max-width: 767.98px) {
  .compute-add {
    padding: 16px;
  }

  .compute-row {
    padding: 12px 8px 12px 16px;
  }

  .compute-row__icon {
    display: none;
  }
}
</style>
