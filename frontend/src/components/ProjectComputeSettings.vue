<script setup lang="ts">
// 项目只给默认：新 agent 开工时用哪台工作电脑。已经在干活的 agent 各有各的机器，
// 改默认不搬它们；它们现在在哪，写在「现在的分布」里。
import type { ComputeChoice, ProjectComputeConfigs } from '../cx_types'

import { onMounted, ref, watch } from 'vue'

import { holdRevealGate } from '@/composables/useRevealGate'

import { getProjectComputeConfigs, saveProjectComputeConfigs } from '../api'
import { t } from '../i18n'
import { choiceDetail } from '../lib/computeConfig'

import ComputeChoiceForm from './ComputeChoiceForm.vue'
import DeviceSessionsSwitch from './DeviceSessionsSwitch.vue'

const props = defineProps<{ projectId: string }>()
const state = ref<ProjectComputeConfigs | null>(null)
const error = ref('')
const busy = ref(false)
const editing = ref(false)
// English says 1 agent / 2 agents; the count's own key carries the plural.
const agents = (count: number) => t('work.projectMachine.agents', { count })
async function load() {
  error.value = ''
  try {
    state.value = await getProjectComputeConfigs(props.projectId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.projectMachine.loadFailed')
  }
}
async function save(choice: ComputeChoice) {
  if (!state.value?.can_manage) return
  busy.value = true
  error.value = ''
  try {
    const result = await saveProjectComputeConfigs(props.projectId, { default: choice })
    state.value = { ...state.value, ...result }
    window.dispatchEvent(new Event('project-compute-updated'))
    editing.value = false
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.projectMachine.saveFailed')
  } finally {
    busy.value = false
  }
}
// 首次取数期间占住设置页的显示闸，见 useRevealGate。
const releaseGate = holdRevealGate()
onMounted(() => load().finally(releaseGate))
watch(() => props.projectId, load)
</script>

<template>
  <div>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
    <template v-if="state">
      <div class="default-row" data-testid="project-default">
        <span class="c-muted">{{ t('work.projectMachine.defaultLabel') }}</span>
        <span class="default-name">{{ state.default.name }}</span>
        <span class="c-muted">{{ choiceDetail(state.default) }}</span>
        <v-btn
          v-if="state.can_manage"
          size="small"
          variant="text"
          class="ml-auto"
          :disabled="busy"
          @click="editing = !editing"
          >{{ editing ? t('work.projectMachine.collapse') : t('work.projectMachine.change') }}</v-btn
        >
      </div>
      <p class="t-body c-muted mt-1 mb-2">{{ t('work.projectMachine.hint') }}</p>
      <ComputeChoiceForm
        v-if="editing && state.can_manage"
        :devices="state.devices"
        :cloud-available="state.cloud_available"
        :project-id="projectId"
        :busy="busy"
        @select="save"
      />

      <div class="distribution" data-testid="project-distribution">
        <div class="distribution-title">{{ t('work.projectMachine.distribution') }}</div>
        <p v-if="!state.distribution.cloud && !state.distribution.devices.length" class="c-muted mb-0">
          {{ t('work.projectMachine.noneStarted') }}
        </p>
        <ul v-else class="distribution-list">
          <li v-if="state.distribution.cloud">
            <span class="status-dot" />{{
              t('work.projectMachine.onCloud', { agents: agents(state.distribution.cloud) })
            }}
          </li>
          <li v-for="device in state.distribution.devices" :key="device.device_id ?? device.name">
            <span class="status-dot" :class="{ 'status-dot--warn': device.machine_access }" />{{
              t('work.projectMachine.onDevice', { name: device.name, agents: agents(device.agents) })
            }}<template v-if="device.machine_access"> · {{ t('work.roomMachine.wholeMachine') }}</template>
            <DeviceSessionsSwitch
              v-if="state.can_manage && device.device_id"
              :project-id="projectId"
              :device="{ device_id: device.device_id, name: device.name }"
              :devices="state.devices"
              :cloud-available="state.cloud_available"
              :project-default="state.default"
              @changed="load"
            />
          </li>
        </ul>
      </div>
      <p v-if="!state.can_manage" class="c-muted mt-3 mb-0">{{ t('work.projectMachine.managedBy') }}</p>
    </template>
  </div>
</template>

<style scoped>
.default-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.default-name {
  color: var(--ink);
  font-weight: 500;
}
.distribution {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}
.distribution-title {
  margin-bottom: 8px;
  color: var(--ink);
  font-weight: 500;
}
.distribution-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.distribution-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  color: var(--text);
}
</style>
