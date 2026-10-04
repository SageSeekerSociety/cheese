<script setup lang="ts">
import type { EnvironmentStatus, ProjectEnvironmentInfo } from '../cx_types'

import { onBeforeUnmount, ref, watch } from 'vue'

import { holdRevealGate } from '@/composables/useRevealGate'
import { useSaveState } from '@/composables/useSaveState'

import { applyRoomEnvironment, getProjectEnvironment, getRoomEnvironment, saveProjectEnvironment } from '../api'

import BaseButton from '@/components/base/BaseButton.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'
import i18n, { t } from '@/i18n'
import { responseText } from '@/lib/noticeText'

const props = defineProps<{ projectId: string }>()
const info = ref<ProjectEnvironmentInfo | null>(null)
const setup = ref('')
const startup = ref('')
const variables = ref<{ key: string; value: string }[]>([])
const selectedRoom = ref<string | null>(null)
const status = ref<EnvironmentStatus | null>(null)
const error = ref('')
// 保存脚本与环境变量：一直留在屏幕上的设置区块，结果就地回执（§3.11）。
const {
  saving,
  saved,
  error: saveError,
  run: runSave,
} = useSaveState({
  feedback: 'inline',
  messages: {
    saved: t('work.projectSettings.environment.saved'),
    failed: t('work.projectSettings.environment.saveFailed'),
  },
})
// 应用（部署一个版本）是一次性动作：结果跟这一次点击走，用 toast 说一声。
const { saving: applying, run: runApply } = useSaveState({
  feedback: 'toast',
  messages: {
    saved: t('work.projectSettings.environment.applyScheduled'),
    failed: t('work.projectSettings.environment.applyFailed'),
  },
})
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let generation = 0
const stateLabel = (state: EnvironmentStatus['state']) => t(`work.projectSettings.environment.state.${state}`)

async function load() {
  const current = ++generation
  error.value = ''
  info.value = null
  selectedRoom.value = null
  try {
    const result = await getProjectEnvironment(props.projectId)
    if (disposed || current !== generation) return
    info.value = result
    setup.value = result.config.setup_script
    startup.value = result.config.startup_script
    variables.value = Object.entries(result.config.variables).map(([key, value]) => ({ key, value }))
    selectedRoom.value = result.rooms[0]?.id ?? null
  } catch (e) {
    if (current === generation)
      error.value = e instanceof Error ? e.message : t('work.projectSettings.environment.loadFailed')
  }
}

async function refreshStatus() {
  clearTimeout(timer)
  const room = selectedRoom.value
  const current = generation
  if (!room || disposed) return
  try {
    const result = await getRoomEnvironment(props.projectId, room)
    if (disposed || current !== generation || room !== selectedRoom.value) return
    status.value = result
  } catch (e) {
    if (current === generation && room === selectedRoom.value)
      error.value = e instanceof Error ? e.message : t('work.projectSettings.environment.statusFailed')
  } finally {
    if (!disposed && current === generation && room === selectedRoom.value) timer = setTimeout(refreshStatus, 5000)
  }
}

async function save() {
  await runSave(async () => {
    const values: Record<string, string> = Object.create(null)
    for (const row of variables.value) {
      if (!row.key || Object.hasOwn(values, row.key)) throw new Error(t('work.projectSettings.environment.varInvalid'))
      values[row.key] = row.value
    }
    const config = await saveProjectEnvironment(props.projectId, {
      setup_script: setup.value,
      startup_script: startup.value,
      variables: values,
    })
    if (info.value) info.value.config = config
  })
}

async function apply(latest: boolean) {
  const room = selectedRoom.value
  if (!room) return
  await runApply(async () => {
    await applyRoomEnvironment(props.projectId, room, latest)
    await refreshStatus()
  })
}

// 首次取数期间占住设置页的显示闸，见 useRevealGate。
const releaseGate = holdRevealGate()
watch(
  () => props.projectId,
  () => load().finally(releaseGate),
  { immediate: true }
)
watch(selectedRoom, () => {
  status.value = null
  void refreshStatus()
})
onBeforeUnmount(() => {
  disposed = true
  clearTimeout(timer)
})
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-console</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.environment.title') }}</span>
    </div>
    <div class="page-section-body">
      <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
      <v-progress-linear v-if="!info && !error" indeterminate />
      <BaseButton v-if="!info && error" kind="secondary" @click="load">{{
        t('work.projectSettings.environment.reload')
      }}</BaseButton>
      <template v-if="info">
        <p class="t-body c-muted mb-3">
          {{ t('work.projectSettings.environment.intro') }}
        </p>
        <v-textarea
          v-model="setup"
          autocomplete="off"
          :label="t('work.projectSettings.environment.setupLabel')"
          variant="outlined"
          rows="5"
          :readonly="!info.can_edit"
          :hint="t('work.projectSettings.environment.setupHint')"
          persistent-hint
          class="mb-4"
        />
        <v-textarea
          v-model="startup"
          autocomplete="off"
          :label="t('work.projectSettings.environment.startupLabel')"
          variant="outlined"
          rows="5"
          :readonly="!info.can_edit"
          :hint="t('work.projectSettings.environment.startupHint')"
          persistent-hint
          class="mb-4"
        />
        <details class="t-body c-muted mb-3">
          <summary>{{ t('work.projectSettings.environment.howTitle') }}</summary>
          <p>{{ t('work.projectSettings.environment.how1') }}</p>
          <p class="mt-2">{{ t('work.projectSettings.environment.how2') }}</p>
          <p class="mt-2">{{ t('work.projectSettings.environment.how3') }}</p>
        </details>
        <p class="t-body mb-2">{{ t('work.projectSettings.environment.varsTitle') }}</p>
        <p class="t-body c-muted mb-3">{{ t('work.projectSettings.environment.varsHint') }}</p>
        <div v-for="(row, index) in variables" :key="index" class="env-var mb-2">
          <v-text-field
            v-model="row.key"
            autocomplete="off"
            :label="t('work.projectSettings.environment.varName')"
            variant="outlined"
            density="compact"
            :readonly="!info.can_edit"
          />
          <v-textarea
            v-model="row.value"
            autocomplete="off"
            :label="t('work.projectSettings.environment.varValue')"
            variant="outlined"
            density="compact"
            rows="1"
            auto-grow
            :readonly="!info.can_edit"
          />
          <BaseButton
            v-if="info.can_edit"
            icon="mdi-close"
            size="sm"
            :aria-label="t('work.projectSettings.environment.removeVar')"
            class="env-var__remove"
            @click="variables.splice(index, 1)"
          />
        </div>
        <div v-if="info.can_edit" class="d-flex ga-2 align-center mb-3">
          <BaseButton kind="secondary" @click="variables.push({ key: '', value: '' })">{{
            t('work.projectSettings.environment.addVar')
          }}</BaseButton>
          <BaseButton kind="primary" :loading="saving" @click="save">{{
            t('work.projectSettings.environment.save')
          }}</BaseButton>
          <SaveStatus
            :saving="saving"
            :saved="saved"
            :error="saveError"
            :saved-text="t('work.projectSettings.environment.saved')"
          />
        </div>
        <p class="t-body c-muted mb-3">{{ t('work.projectSettings.environment.saveNote') }}</p>
        <details class="t-body c-faint mb-4">
          <summary>{{ t('work.projectSettings.environment.savedConfig') }}</summary>
          {{ t('work.projectSettings.environment.revision', { rev: info.config.revision.slice(0, 12) }) }}
        </details>
        <template v-if="info.rooms.length">
          <v-select
            v-model="selectedRoom"
            autocomplete="off"
            :items="info.rooms"
            item-title="title"
            item-value="id"
            :label="t('work.projectSettings.environment.roomSelect')"
            variant="outlined"
            density="compact"
          >
            <!-- 房间名是人起的，不跟着界面语言变。 -->
            <template #selection="{ item }">
              <span data-user-content>{{ item.title }}</span>
            </template>
          </v-select>
          <p v-if="status" class="t-body mb-2">
            {{ stateLabel(status.state)
            }}<span v-if="status.stage">
              ·
              {{
                t(
                  `work.projectSettings.environment.stage.${status.stage === 'setup' || status.stage === 'startup' ? status.stage : 'done'}`
                )
              }}</span
            >
          </p>
          <p v-if="status?.error" class="t-body mb-2">
            {{ status.error
            }}<span v-if="status.exit_code != null">{{
              t('work.projectSettings.environment.exitCode', { code: status.exit_code })
            }}</span>
          </p>
          <details v-if="status" class="t-body c-muted mb-2">
            <summary>{{ t('work.projectSettings.environment.detailsTitle') }}</summary>
            <p>
              {{
                t('work.projectSettings.environment.revision', {
                  rev: status.pinned_revision?.slice(0, 12) ?? t('work.projectSettings.environment.unpinned'),
                })
              }}
            </p>
            <p v-if="status.started_at">
              {{
                t('work.projectSettings.environment.started', {
                  at: new Date(status.started_at).toLocaleString(i18n.global.locale.value),
                })
              }}
              <span v-if="status.finished_at">{{
                t('work.projectSettings.environment.finished', {
                  at: new Date(status.finished_at).toLocaleString(i18n.global.locale.value),
                })
              }}</span>
            </p>
            <p>{{ t('work.projectSettings.environment.logNote') }}</p>
          </details>
          <p v-if="status?.recovery_state === 'requested'" class="t-body mb-2">
            {{ t('work.projectSettings.environment.recoveryRequested') }}
          </p>
          <p v-else-if="status?.recovery_state === 'retrying'" class="t-body mb-2">
            {{ t('work.projectSettings.environment.recoveryRetrying') }}
          </p>
          <p v-else-if="status?.recovery_state === 'needs_help'" class="t-body mb-2">
            {{ t('work.projectSettings.environment.recoveryNeedsHelp') }}
          </p>
          <div class="d-flex flex-wrap ga-2 mb-3">
            <BaseButton kind="secondary" @click="refreshStatus">{{
              t('work.projectSettings.environment.refreshStatus')
            }}</BaseButton>
            <BaseButton
              v-if="info.can_edit"
              kind="primary"
              :loading="applying"
              :disabled="saving || status?.busy || status?.state === 'preparing'"
              @click="apply(true)"
              >{{ t('work.projectSettings.environment.applyNext') }}</BaseButton
            >
            <BaseButton
              v-if="info.can_edit && status?.state === 'failed'"
              kind="secondary"
              :disabled="applying || status?.busy"
              @click="apply(false)"
              >{{ t('work.projectSettings.environment.retryNext') }}</BaseButton
            >
          </div>
          <p class="t-body c-faint mb-2">{{ t('work.projectSettings.environment.roomNote') }}</p>
          <pre v-if="status?.log" class="environment-log">{{ responseText(status, 'log') }}</pre>
        </template>
      </template>
    </div>
  </section>
</template>

<style scoped>
/* 名称、值、删除排成一行；内容列窄到两格都放不下名字时，名称和值上下叠起来，删除
   留在右边，两个输入框都拿到整行的宽度。 */
.env-var {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: start;
  gap: 8px;
}
@container (width < 480px) {
  .env-var {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .env-var__remove {
    grid-row: 1 / span 2;
    grid-column: 2;
  }
}
.environment-log {
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
}
</style>
