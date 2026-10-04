<script setup lang="ts">
// 项目只给默认：新 agent 开工时用哪台工作电脑。已经在干活的 agent 各有各的机器，
// 改默认不搬它们；它们现在在哪，写在「现在的分布」里。
import type { ComputeChoice, ProjectComputeConfigs } from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'

import { holdRevealGate } from '@/composables/useRevealGate'

import { getProjectComputeConfigs, saveProjectComputeConfigs } from '../api'
import { useCloudSupply } from '../composables/useCloudSupply'
import { t } from '../i18n'
import { choiceDetail, choiceName, deviceName } from '../lib/computeConfig'

import ComputeChoiceForm from './ComputeChoiceForm.vue'
import DeviceSessionsSwitch from './DeviceSessionsSwitch.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import SettingsRow from '@/components/base/SettingsRow.vue'
import FirstTimeHint from '@/components/common/FirstTimeHint.vue'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()
const workspace = useWorkspaceStore()
// 「用自己的电脑」要走三处：个人设置里接入、团队工作电脑页加进来、回这里选。项目
// 还一台设备都没有的时候，把前两处的路指出来。
const teamHandle = computed(() => workspace.projects.find((p) => p.id === props.projectId)?.team_handle ?? null)
const { supply: cloudSupply, loading: supplyLoading, load: loadSupply } = useCloudSupply(() => props.projectId)
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
// 再进编辑就再问一次范围：表单收起时答案留在这里，留着的那份会旧。还没问过就不问，
// 等表单自己要；这样一次编辑最多问一次。
watch(editing, (open) => {
  if (open && cloudSupply.value) void loadSupply()
})
</script>

<template>
  <div>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
    <template v-if="state">
      <!-- The label sits outside the value group (SettingsRow); the value, its
           detail and the change button stay in one line as the row's control. -->
      <SettingsRow data-testid="project-default" :label="t('work.projectMachine.defaultLabel')" width="list">
        <div class="default-row">
          <span class="default-name">{{ choiceName(state.default) }}</span>
          <span class="c-muted">{{ choiceDetail(state.default) }}</span>
          <BaseButton
            v-if="state.can_manage"
            kind="secondary"
            size="sm"
            class="ml-auto"
            :disabled="busy"
            @click="editing = !editing"
            >{{ editing ? t('work.projectMachine.collapse') : t('work.projectMachine.change') }}</BaseButton
          >
        </div>
      </SettingsRow>
      <p class="t-body c-muted mt-1 mb-2">{{ t('work.projectMachine.hint') }}</p>
      <FirstTimeHint v-if="state.can_manage && !state.devices.length" id="own-device">
        <i18n-t keypath="global.firstHint.ownDevice" scope="global" tag="span">
          <template #devices>
            <router-link :to="{ name: 'UserSettingsDevices' }">{{
              t('global.firstHint.ownDeviceDevices')
            }}</router-link>
          </template>
          <template #teamCompute>
            <router-link v-if="teamHandle" :to="{ name: 'TeamsDetailCompute', params: { handle: teamHandle } }">{{
              t('global.firstHint.ownDeviceTeamCompute')
            }}</router-link>
            <template v-else>{{ t('global.firstHint.ownDeviceTeamCompute') }}</template>
          </template>
        </i18n-t>
      </FirstTimeHint>
      <ComputeChoiceForm
        v-if="editing && state.can_manage"
        :devices="state.devices"
        :cloud-available="state.cloud_available"
        :supply="cloudSupply"
        :supply-loading="supplyLoading"
        :busy="busy"
        @select="save"
        @need-supply="loadSupply"
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
          <li v-for="device in state.distribution.devices" :key="device.device_id ?? ''">
            <span class="status-dot" :class="{ 'status-dot--warn': device.machine_access }" />{{
              t('work.projectMachine.onDevice', {
                name: deviceName(device.name, device.device_id),
                agents: agents(device.agents),
              })
            }}<template v-if="device.machine_access"> · {{ t('work.roomMachine.wholeMachine') }}</template>
            <DeviceSessionsSwitch
              v-if="state.can_manage && device.device_id"
              :project-id="projectId"
              :device="{ device_id: device.device_id, name: deviceName(device.name, device.device_id) }"
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
  flex: 1;
  flex-wrap: wrap;
  min-width: 0;
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
