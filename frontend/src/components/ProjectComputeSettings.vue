<script setup lang="ts">
// 项目只给默认：新会话开工时用哪个环境。已经在干活的会话各有各的环境，改默认不搬
// 它们；它们现在在哪，写在「现在的分布」里。平台挑的机器只数个数，不点名。
// 取数与保存由页面接线；设备会话入口也由页面传入，这里只转发 changed。
import type { Component } from 'vue'
import type { Environment, ProjectComputeConfigs } from '../types/compute'

import { t } from '../i18n'
import { environmentName } from '../lib/environment'

import EnvironmentPicker from './EnvironmentPicker.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import SettingsRow from '@/components/base/SettingsRow.vue'

defineProps<{
  projectId: string
  deviceSessionsComponent?: Component
  state: ProjectComputeConfigs | null
  error: string
  busy: boolean
  editing: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
  (e: 'save', env: Environment): void
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
          <span class="default-name">{{ environmentName(state.default, state.devices, state.specs) }}</span>
          <BaseButton kind="secondary" size="sm" class="ml-auto" :disabled="busy" @click="emit('toggle')">{{
            editing ? t('work.projectMachine.collapse') : t('work.projectMachine.change')
          }}</BaseButton>
        </div>
      </SettingsRow>
      <p class="t-body c-muted mt-1 mb-2">{{ t('work.projectMachine.hint') }}</p>
      <EnvironmentPicker
        v-if="editing"
        class="mt-2"
        :current="state.default"
        :devices="state.devices"
        :specs="state.specs"
        :busy="busy"
        @select="(env) => emit('save', env)"
        @cancel="emit('toggle')"
      />

      <div class="distribution" data-testid="project-distribution">
        <div class="distribution-title">{{ t('work.projectMachine.distribution') }}</div>
        <p v-if="!state.distribution.sandboxes.length && !state.distribution.devices.length" class="c-muted mb-0">
          {{ t('work.projectMachine.noneStarted') }}
        </p>
        <ul v-else class="distribution-list">
          <li v-for="group in state.distribution.sandboxes" :key="group.spec">
            <span class="status-dot" />{{
              t('work.projectMachine.onPlatform', {
                env: t('compute.env.sandboxOf', { spec: group.label }),
                agents: agents(group.agents),
              })
            }}
          </li>
          <li v-for="device in state.distribution.devices" :key="device.device_id ?? ''">
            <span class="status-dot" />{{
              t('work.projectMachine.onDevice', {
                name: device.name ?? t('compute.env.removedDevice'),
                agents: agents(device.agents),
              })
            }}
            <component
              :is="deviceSessionsComponent"
              v-if="deviceSessionsComponent && state.can_manage"
              :project-id="projectId"
              :device="{ device_id: device.device_id, name: device.name ?? t('compute.env.removedDevice') }"
              :devices="state.devices"
              :specs="state.specs"
              @changed="emit('changed')"
            />
          </li>
        </ul>
      </div>
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
