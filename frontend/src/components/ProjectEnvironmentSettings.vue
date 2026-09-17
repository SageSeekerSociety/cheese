<script setup lang="ts">
import type { EnvironmentStatus, ProjectEnvironmentInfo } from '../cx_types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { applyRoomEnvironment, getProjectEnvironment, getRoomEnvironment, saveProjectEnvironment } from '../api'

const props = defineProps<{ projectId: string }>()
const { t, locale } = useI18n()
const info = ref<ProjectEnvironmentInfo | null>(null)
const setup = ref('')
const startup = ref('')
const variables = ref<{ key: string; value: string }[]>([])
const selectedRoom = ref<string | null>(null)
const status = ref<EnvironmentStatus | null>(null)
const error = ref('')
const notice = ref('')
const saving = ref(false)
const applying = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let generation = 0
// computed 而不是模块级常量表：常量表在 setup 时求值一次，切语言不会重算，
// 状态文案就会一直停在旧语言。
const labels = computed<Record<EnvironmentStatus['state'], string>>(() => ({
  unbound: t('projects.environment.status.unbound'),
  pending: t('projects.environment.status.pending'),
  preparing: t('projects.environment.status.preparing'),
  ready: t('projects.environment.status.ready'),
  failed: t('projects.environment.status.failed'),
  stopped: t('projects.environment.status.stopped'),
  offline: t('projects.environment.status.offline'),
}))

/** 时间按界面语言格式化：不传语言，en 界面里会冒出中文的日期格式。 */
function formatTime(iso: string) {
  return new Date(iso).toLocaleString(locale.value === 'en' ? 'en' : 'zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

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
    if (current === generation) error.value = e instanceof Error ? e.message : t('projects.environment.loadFailed')
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
      error.value = e instanceof Error ? e.message : t('projects.environment.statusLoadFailed')
  } finally {
    if (!disposed && current === generation && room === selectedRoom.value) timer = setTimeout(refreshStatus, 5000)
  }
}

async function save() {
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const values: Record<string, string> = Object.create(null)
    for (const row of variables.value) {
      if (!row.key || Object.hasOwn(values, row.key)) throw new Error(t('projects.environment.variableNameInvalid'))
      values[row.key] = row.value
    }
    const config = await saveProjectEnvironment(props.projectId, {
      setup_script: setup.value,
      startup_script: startup.value,
      variables: values,
    })
    if (info.value) info.value.config = config
    notice.value = t('projects.environment.savedNotice')
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('projects.environment.saveFailed')
  } finally {
    saving.value = false
  }
}

async function apply(latest: boolean) {
  if (!selectedRoom.value) return
  applying.value = true
  error.value = ''
  notice.value = ''
  try {
    await applyRoomEnvironment(props.projectId, selectedRoom.value, latest)
    notice.value = t('projects.environment.scheduledNotice')
    await refreshStatus()
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('projects.environment.applyFailed')
  } finally {
    applying.value = false
  }
}

watch(() => props.projectId, load, { immediate: true })
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
      <span class="page-section-title" data-section="environment">{{ t('projects.environment.title') }}</span>
    </div>
    <div class="page-section-body">
      <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
      <v-alert v-if="notice" type="success" variant="tonal" class="mb-3">{{ notice }}</v-alert>
      <v-progress-linear v-if="!info && !error" indeterminate />
      <v-btn v-if="!info && error" variant="text" @click="load">{{ t('projects.environment.reload') }}</v-btn>
      <template v-if="info">
        <p class="t-body c-muted mb-3">
          {{ t('projects.environment.intro') }}
        </p>
        <v-textarea
          v-model="setup"
          autocomplete="off"
          :label="t('projects.environment.setupLabel')"
          variant="outlined"
          rows="5"
          :readonly="!info.can_edit"
          :hint="t('projects.environment.setupHint')"
          persistent-hint
          class="mb-4"
        />
        <v-textarea
          v-model="startup"
          autocomplete="off"
          :label="t('projects.environment.startupLabel')"
          variant="outlined"
          rows="5"
          :readonly="!info.can_edit"
          :hint="t('projects.environment.startupHint')"
          persistent-hint
          class="mb-4"
        />
        <details class="t-body c-muted mb-3">
          <summary>{{ t('projects.environment.howItWorksSummary') }}</summary>
          <p>{{ t('projects.environment.howItWorks') }}</p>
          <p class="mt-2">
            {{ t('projects.environment.howItWorksHome') }}
          </p>
        </details>
        <p class="t-body mb-2">{{ t('projects.environment.variablesTitle') }}</p>
        <p class="t-body c-muted mb-3">{{ t('projects.environment.variablesHint') }}</p>
        <div v-for="(row, index) in variables" :key="index" class="d-flex align-start ga-2 mb-2">
          <v-text-field
            v-model="row.key"
            autocomplete="off"
            :label="t('projects.environment.varName')"
            variant="outlined"
            density="compact"
            :readonly="!info.can_edit"
          />
          <v-textarea
            v-model="row.value"
            autocomplete="off"
            :label="t('projects.environment.varValue')"
            variant="outlined"
            density="compact"
            rows="1"
            auto-grow
            :readonly="!info.can_edit"
          />
          <v-btn
            v-if="info.can_edit"
            icon="mdi-close"
            variant="text"
            size="small"
            :aria-label="t('projects.environment.removeVariable')"
            @click="variables.splice(index, 1)"
          />
        </div>
        <div v-if="info.can_edit" class="d-flex ga-2 mb-3">
          <v-btn variant="text" @click="variables.push({ key: '', value: '' })">{{
            t('projects.environment.addVariable')
          }}</v-btn>
          <v-btn color="primary" :loading="saving" @click="save">{{ t('projects.environment.save') }}</v-btn>
        </div>
        <p class="t-body c-muted mb-3">{{ t('projects.environment.saveHint') }}</p>
        <details class="t-body c-faint mb-4">
          <summary>{{ t('projects.environment.savedConfigSummary') }}</summary>
          {{ t('projects.environment.version', { revision: info.config.revision.slice(0, 12) }) }}
        </details>
        <template v-if="info.rooms.length">
          <v-select
            v-model="selectedRoom"
            autocomplete="off"
            :items="info.rooms"
            item-title="title"
            item-value="id"
            :label="t('projects.environment.viewRoomEnvironment')"
            variant="outlined"
            density="compact"
          />
          <p v-if="status" class="t-body mb-2">
            {{ labels[status.state]
            }}<span v-if="status.stage">
              ·
              {{
                status.stage === 'setup'
                  ? t('projects.environment.stageSetup')
                  : status.stage === 'startup'
                    ? t('projects.environment.stageStartup')
                    : t('projects.environment.stageDone')
              }}</span
            >
          </p>
          <p v-if="status?.error" class="t-body mb-2">
            {{ status.error
            }}<span v-if="status.exit_code != null">{{
              t('projects.environment.exitCode', { code: status.exit_code })
            }}</span>
          </p>
          <details v-if="status" class="t-body c-muted mb-2">
            <summary>{{ t('projects.environment.detailsSummary') }}</summary>
            <p>
              {{
                t('projects.environment.version', {
                  revision: status.pinned_revision?.slice(0, 12) ?? t('projects.environment.notBound'),
                })
              }}
            </p>
            <p v-if="status.started_at">
              {{ t('projects.environment.startedAt', { time: formatTime(status.started_at) }) }}
              <span v-if="status.finished_at">
                · {{ t('projects.environment.finishedAt', { time: formatTime(status.finished_at) }) }}</span
              >
            </p>
            <p>{{ t('projects.environment.logNote') }}</p>
          </details>
          <p v-if="status?.recovery_state === 'requested'" class="t-body mb-2">
            {{ t('projects.environment.recoveryRequested') }}
          </p>
          <p v-else-if="status?.recovery_state === 'retrying'" class="t-body mb-2">
            {{ t('projects.environment.recoveryRetrying') }}
          </p>
          <p v-else-if="status?.recovery_state === 'needs_help'" class="t-body mb-2">
            {{ t('projects.environment.recoveryNeedsHelp') }}
          </p>
          <div class="d-flex flex-wrap ga-2 mb-3">
            <v-btn variant="text" @click="refreshStatus">{{ t('projects.environment.refreshStatus') }}</v-btn>
            <v-btn
              v-if="info.can_edit"
              variant="outlined"
              :loading="applying"
              :disabled="saving || status?.busy || status?.state === 'preparing'"
              @click="apply(true)"
              >{{ t('projects.environment.applyNextStart') }}</v-btn
            >
            <v-btn
              v-if="info.can_edit && status?.state === 'failed'"
              variant="text"
              :disabled="applying || status?.busy"
              @click="apply(false)"
              >{{ t('projects.environment.retryNextStart') }}</v-btn
            >
          </div>
          <p class="t-body c-faint mb-2">{{ t('projects.environment.applyNote') }}</p>
          <pre v-if="status?.log" class="environment-log">{{ status.log }}</pre>
        </template>
      </template>
    </div>
  </section>
</template>

<style scoped>
.environment-log {
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
}
</style>
