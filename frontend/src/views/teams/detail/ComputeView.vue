<!--
  The rendering half of Compute.vue, split out by `pnpm run scene:split`: props in,
  events out. The page keeps the route, the requests and the stores.
-->

<script setup lang="ts">
import type { MyDevice } from '@/types/devices'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { screenAgentName, teammateName } from '@/lib/agentNames'
import { topicTitle } from '@/lib/topicState'

defineProps<{
  scope: 'own' | 'team'
  loading: boolean
  loadFailed: boolean
  /** 读失败那句话，和 401/403 的判断都由页面算好——这一半只负责画。 */
  reason: string | null
  forbidden: boolean
  addable: MyDevice[]
  busy: string | null
  myDevices: MyDevice[]
  devices: MyDevice[]
  onlineCount: number
  teamId: number
  myDeviceIds: Set<string>
}>()
const emit = defineEmits<{
  load: []
  'add-machine': [device: MyDevice]
  'remove-machine': [device: MyDevice]
}>()
const error = defineModel<string | null>('error', { required: true })
</script>

<template>
  <v-container class="px-6 py-5" fluid>
    <div class="mb-5 d-flex align-start flex-wrap ga-3">
      <div>
        <p class="text-body-2 text-medium-emphasis mb-0">
          {{ t(`teams.compute.${scope}.subtitle`) }}
        </p>
      </div>
    </div>

    <!-- 写操作（添加/移除）失败是可关的一条提示，页面本身还在。读失败不是：整块内容
         没读到，就得换成失败的样子，不能一边弹条一边画「还没有自己的机器」。 -->
    <v-alert v-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="error = null">
      {{ error }}
    </v-alert>

    <div v-if="loading" class="py-12 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <BaseLoadError
      v-else-if="loadFailed"
      :title="t('teams.compute.loadFailed')"
      :error="reason"
      :forbidden="forbidden"
      @retry="emit('load')"
    />

    <template v-else>
      <section class="compute-section">
        <div class="section-heading mb-3">
          <div>
            <h3 class="text-subtitle-1 font-weight-medium">{{ t('teams.compute.selfHostedSection') }}</h3>
            <p class="text-caption text-medium-emphasis mb-0">{{ t(`teams.compute.${scope}.selfHostedSubtitle`) }}</p>
          </div>
          <v-menu location="bottom end">
            <template #activator="{ props: menuProps }">
              <BaseButton v-bind="menuProps" kind="secondary" prepend-icon="mdi-plus">{{
                t('teams.compute.addDevice')
              }}</BaseButton>
            </template>
            <v-list density="compact" min-width="280">
              <v-list-item
                v-for="device in addable"
                :key="device.device_id"
                :title="device.name"
                :disabled="busy === device.device_id"
                @click="emit('add-machine', device)"
              >
                <template #prepend>
                  <v-icon :color="device.online ? 'success' : 'grey'" size="12" class="mr-2">mdi-circle</v-icon>
                </template>
              </v-list-item>
              <v-list-item
                v-if="!addable.length && myDevices.length"
                disabled
                :title="t(`teams.compute.${scope}.allDevicesAdded`)"
              />
              <v-list-item
                v-if="!myDevices.length"
                :to="{ name: 'UserSettingsDevices' }"
                :title="t('teams.compute.goToMyDevices')"
              >
                <template #prepend><v-icon size="18" class="mr-2">mdi-laptop-account</v-icon></template>
              </v-list-item>
            </v-list>
          </v-menu>
        </div>

        <div v-if="!devices.length" class="empty-panel">
          <BaseEmptyState
            size="compact"
            icon="mdi-laptop-off"
            :title="t('teams.compute.selfHostedEmptyTitle')"
            :desc="t(`teams.compute.${scope}.selfHostedEmptyHint`)"
          />
        </div>
        <template v-else>
          <div class="text-caption text-medium-emphasis mb-3">
            {{ t('teams.compute.deviceSummary', { count: devices.length, online: onlineCount }) }}
          </div>
          <v-row>
            <v-col v-for="device in devices" :key="device.device_id" cols="12" sm="6" lg="4">
              <v-card variant="outlined" rounded="lg" class="pa-4 fill-height">
                <div class="d-flex align-center mb-2">
                  <v-icon :color="device.online ? 'success' : 'grey'" size="20" class="mr-2">mdi-laptop</v-icon>
                  <span class="text-subtitle-2 font-weight-medium text-truncate">{{ device.name }}</span>
                  <v-spacer />
                  <span
                    class="text-caption"
                    :class="device.online ? 'text-success font-weight-medium' : 'text-medium-emphasis'"
                  >
                    <span class="status-dot" :class="device.online ? 'status-dot--on' : ''" />
                    {{ device.online ? t('teams.compute.online') : t('teams.compute.offline') }}
                  </span>
                </div>
                <div class="text-caption text-medium-emphasis machine-id">{{ device.device_id }}</div>
                <div v-if="!device.team_ids.includes(teamId)" class="mt-2 device-user">
                  {{
                    t('work.deviceInUse.attached', {
                      projects: (device.attached_projects ?? [])
                        .map((project) => project.name)
                        .join(t('teams.compute.listSeparator')),
                    })
                  }}
                </div>
                <div v-if="device.screens.length" class="mt-3 d-flex flex-wrap ga-2">
                  <v-chip
                    v-for="screen in device.screens"
                    :key="screen.sid"
                    size="x-small"
                    variant="tonal"
                    color="primary"
                    :data-user-content="screen.agent_name || undefined"
                  >
                    <v-icon start size="12">mdi-monitor-eye</v-icon>
                    {{ t('teams.compute.screenRunning', { name: screenAgentName(screen) }) }}
                  </v-chip>
                </div>
                <div
                  v-for="use in device.in_use ?? []"
                  :key="`${use.topic_id}:${use.agent_handle}`"
                  class="mt-2 device-user"
                >
                  <!-- `scope="global"`：这一半要能离开页面单独挂（预览站里就是根），
                       `<i18n-t>` 的默认 parent 作用域在根上找不到，会往控制台留一句
                       警告再退回全局。这里本来就只有一个全局词典，直接点名全局。 -->
                  <i18n-t keypath="work.deviceInUse.line" tag="span" scope="global">
                    <template #project
                      ><span data-user-content>{{ use.project_name }}</span></template
                    >
                    <template #room
                      ><span :data-user-content="use.topic_title || undefined">{{
                        topicTitle({ title: use.topic_title })
                      }}</span></template
                    >
                    <template #agent>
                      <UserRef
                        :handle="use.agent_handle"
                        :name="teammateName(use.agent_name, use.agent_name_source)"
                        :project-id="use.project_id"
                      />
                    </template>
                  </i18n-t>
                </div>
                <div
                  v-if="myDeviceIds.has(device.device_id) && device.team_ids.includes(teamId)"
                  class="mt-2 d-flex justify-end"
                >
                  <BaseButton size="sm" :loading="busy === device.device_id" @click="emit('remove-machine', device)">
                    {{ t(`teams.compute.${scope}.remove`) }}
                  </BaseButton>
                </div>
              </v-card>
            </v-col>
          </v-row>
        </template>
      </section>
    </template>
  </v-container>
</template>

<style scoped>
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.profile-grid {
  display: grid;
  /* See NodeBoard: without min(), 240px is a floor the grid keeps even in a
     narrower column, and the board overflows rather than reflowing. */
  grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr));
  gap: 10px;
}
.profile-card {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 76px;
  padding: 12px 14px;
  border: 1px solid rgba(var(--v-border-color), 0.18);
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-surface));
  text-align: left;
}
.profile-card:not(:disabled) {
  cursor: pointer;
}
.profile-card--active {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.04);
}
.profile-card--off {
  opacity: 0.58;
}
.profile-copy {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}
.profile-title {
  font-size: 0.875rem;
  font-weight: 600;
}
.profile-description {
  margin-top: 2px;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 0.75rem;
  line-height: 1.4;
}
.empty-panel {
  border: 1px dashed rgba(var(--v-border-color), 0.24);
  border-radius: var(--radius-lg);
}
.device-user {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.machine-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
  background: rgb(var(--v-theme-on-surface));
  opacity: 0.35;
}
.status-dot--on {
  background: rgb(var(--v-theme-success));
  opacity: 1;
}
@media (max-width: 600px) {
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
