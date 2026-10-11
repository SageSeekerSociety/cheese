<script setup lang="ts">
// 项目只给默认：新 agent 开工时用哪台工作电脑。已经在干活的 agent 各有各的机器，
// 改默认不搬它们；它们现在在哪，写在「现在的分布」里。
//
// 只吃 props、只往上发事件（`frontend_grade.py` 的 A 级）：取数与发请求在
// `composables/useProjectComputeSettings.ts` 里，由设置页接线。
//
// 设备会话入口由设置页传组件对象，放在原来的设备行；这里只转发 changed，
// 不取数，也不依赖入口的具体实现。
import type { Component } from 'vue'
import type { ComputeChoice, ProjectComputeConfigs } from '../types/compute'

import { t } from '../i18n'
import { choiceDetail, choiceName, deviceName } from '../lib/computeConfig'

import ComputeChoiceForm from './ComputeChoiceForm.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import SettingsRow from '@/components/base/SettingsRow.vue'

defineProps<{
  projectId: string
  deviceSessionsComponent?: Component
  /** 读到的算力现状；还没读到是 null。 */
  state: ProjectComputeConfigs | null
  /** 上一句没说完的话：读失败或保存失败。 */
  error: string
  /** 一次保存还在路上。 */
  busy: boolean
  /** 「更换」按下去切到的那一档：展开还是收起那份表单。 */
  editing: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
  (e: 'save', choice: ComputeChoice): void
  (e: 'changed'): void
}>()

// English says 1 agent / 2 agents; the count's own key carries the plural.
const agents = (count: number) => t('work.projectMachine.agents', { count })
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
            @click="emit('toggle')"
            >{{ editing ? t('work.projectMachine.collapse') : t('work.projectMachine.change') }}</BaseButton
          >
        </div>
      </SettingsRow>
      <p class="t-body c-muted mt-1 mb-2">{{ t('work.projectMachine.hint') }}</p>
      <ComputeChoiceForm
        v-if="editing && state.can_manage"
        :devices="state.devices"
        :cloud-available="state.cloud_available"
        :cloud-vm-available="state.cloud_vm_available"
        :busy="busy"
        @select="(choice) => emit('save', choice)"
      />

      <div class="distribution" data-testid="project-distribution">
        <div class="distribution-title">{{ t('work.projectMachine.distribution') }}</div>
        <p
          v-if="!state.distribution.cloud && !state.distribution.cloud_vm && !state.distribution.devices.length"
          class="c-muted mb-0"
        >
          {{ t('work.projectMachine.noneStarted') }}
        </p>
        <ul v-else class="distribution-list">
          <li v-if="state.distribution.cloud">
            <span class="status-dot" />{{
              t('work.projectMachine.onCloud', { agents: agents(state.distribution.cloud) })
            }}
          </li>
          <li v-if="state.distribution.cloud_vm">
            <span class="status-dot" />{{
              t('work.projectMachine.onCloudVm', { agents: agents(state.distribution.cloud_vm) })
            }}
          </li>
          <li v-for="device in state.distribution.devices" :key="device.device_id ?? ''">
            <span class="status-dot" :class="{ 'status-dot--warn': device.machine_access }" />{{
              t('work.projectMachine.onDevice', {
                name: deviceName(device.name, device.device_id),
                agents: agents(device.agents),
              })
            }}<template v-if="device.machine_access"> · {{ t('work.roomMachine.wholeMachine') }}</template>
            <component
              :is="deviceSessionsComponent"
              v-if="deviceSessionsComponent && state.can_manage && device.device_id"
              :project-id="projectId"
              :device="{ device_id: device.device_id, name: deviceName(device.name, device.device_id) }"
              :devices="state.devices"
              :cloud-available="state.cloud_available"
              :project-default="state.default"
              @changed="emit('changed')"
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
