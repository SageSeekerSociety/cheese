<script setup lang="ts">
// 「运行环境」那一块：在哪运行、怎么准备、还能跑多久，一页说完。
//
// 只吃 props、只往上发事件（`frontend_grade.py` 的 A 级）：取数、保存、状态轮询、
// 「让芝士看看」/重试/采用都在 `composables/useProjectEnvironmentSettings.ts` 里，由设置页
// 接线。编辑草稿（那两段脚本与环境变量那几行）留在这一层，整份报上去。
import type { EnvironmentDiagnosis } from '@/types/environment'
import type { EnvironmentStatus, ProjectEnvironmentInfo } from '../cx_types'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'
import EnvironmentFailureCard from '@/components/environment/EnvironmentFailureCard.vue'
import i18n, { t } from '@/i18n'
import { responseText } from '@/lib/noticeText'

/** 报给设置页的那一份配置：两段脚本，和环境变量那几行（查重在那一层做）。 */
interface ConfigDraft {
  setup_script: string
  startup_script: string
  variables: { key: string; value: string }[]
}

const props = defineProps<{
  /** 读到的环境配置；还没读到是 null。 */
  info: ProjectEnvironmentInfo | null
  /** 选中那个频道最近一次准备的现状；还没读到是 null。 */
  status: EnvironmentStatus | null
  /** 现在看的是哪个频道。 */
  selectedRoom: string | null
  /** 上一句没说完的话：读失败、状态读失败或诊断失败。 */
  error: string
  /** 脚本与环境变量一次保存在路上。 */
  saving: boolean
  /** 刚保存成功（那一行回执还亮着）。 */
  saved: boolean
  /** 保存失败：那一句原话。 */
  saveError: string
  /** 一次「应用」在路上。 */
  applying: boolean
  /** 芝士对这次失败的回答；还没问是 null。 */
  diagnosis: EnvironmentDiagnosis | null
  /** 「让芝士看看」还在等它回答。 */
  diagnosing: boolean
  /** 项目给 AI 队友起的名字。 */
  agentName: string
}>()

const emit = defineEmits<{
  (e: 'reload'): void
  (e: 'select-room', room: string | null): void
  (e: 'refresh-status'): void
  (e: 'save', draft: ConfigDraft): void
  (e: 'apply', latest: boolean): void
  (e: 'diagnose'): void
  (e: 'retry'): void
  (e: 'adopt', draft: ConfigDraft): void
  (e: 'dismiss'): void
}>()

// 本地编辑态：那两段脚本和环境变量那几行。`info` 一换（首次取到、或换项目）就归位。
const setup = ref('')
const startup = ref('')
const variables = ref<{ key: string; value: string }[]>([])

watch(
  () => props.info,
  (info) => {
    if (!info) return
    setup.value = info.config.setup_script
    startup.value = info.config.startup_script
    variables.value = Object.entries(info.config.variables).map(([key, value]) => ({ key, value }))
  },
  { immediate: true }
)

const stateLabel = (state: EnvironmentStatus['state']) => t(`work.projectSettings.environment.state.${state}`)

const failing = computed(() => props.info?.rooms.find((r) => r.id === props.selectedRoom && r.failure) ?? null)

/** 把这一层手上那份配置整份报上去（脚本两段 + 环境变量那几行）。 */
function draft(): ConfigDraft {
  return {
    setup_script: setup.value,
    startup_script: startup.value,
    variables: variables.value.map((row) => ({ ...row })),
  }
}

function save() {
  emit('save', draft())
}

/** 采用芝士的改法：写进本地脚本，整份报上去（先保存，成了再重试，那一层接）。 */
function adopt() {
  const answer = props.diagnosis
  if (!answer) return
  if (answer.setup_script !== null) setup.value = answer.setup_script
  if (answer.startup_script !== null) startup.value = answer.startup_script
  emit('adopt', draft())
}
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
      <BaseButton v-if="!info && error" kind="secondary" @click="emit('reload')">{{
        t('work.projectSettings.environment.reload')
      }}</BaseButton>
      <template v-if="info">
        <EnvironmentFailureCard
          v-if="failing?.failure"
          :failure="failing.failure"
          :room-title="failing.title"
          :can-edit="info.can_edit"
          :agent-name="agentName"
          :diagnosis="diagnosis"
          :diagnosing="diagnosing"
          :retrying="applying || saving"
          @diagnose="emit('diagnose')"
          @retry="emit('retry')"
          @adopt="adopt"
          @dismiss="emit('dismiss')"
        />
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
            :model-value="selectedRoom"
            autocomplete="off"
            :items="info.rooms"
            item-title="title"
            item-value="id"
            :label="t('work.projectSettings.environment.roomSelect')"
            variant="outlined"
            density="compact"
            @update:model-value="(room) => emit('select-room', room)"
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
          <div class="d-flex flex-wrap ga-2 mb-3">
            <BaseButton kind="secondary" @click="emit('refresh-status')">{{
              t('work.projectSettings.environment.refreshStatus')
            }}</BaseButton>
            <BaseButton
              v-if="info.can_edit"
              kind="primary"
              :loading="applying"
              :disabled="saving || status?.busy || status?.state === 'preparing'"
              @click="emit('apply', true)"
              >{{ t('work.projectSettings.environment.applyNext') }}</BaseButton
            >
            <BaseButton
              v-if="info.can_edit && status?.state === 'failed'"
              kind="secondary"
              :disabled="applying || status?.busy"
              @click="emit('apply', false)"
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
