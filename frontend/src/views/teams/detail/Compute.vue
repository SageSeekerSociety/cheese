<script setup lang="ts">
// The team's work computers: every self-hosted device its projects can use,
// whether registered for the team or attached to one of its projects. Cloud
// sandboxes are the platform's, and are not listed here.
import type { MyDevice } from '@/cx_types'

import { computed, inject, onMounted, ref, watch } from 'vue'

import { listMyDevices, listTeamDevices, registerDeviceForTeam, unregisterDeviceFromTeam } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { screenAgentName, teammateName } from '@/lib/agentNames'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { topicTitle } from '@/lib/topicState'
import { useDialog } from '@/plugins/dialog'

const { confirm } = useDialog()
const teamData = inject(teamDataInjectionKey, ref())
const teamId = computed(() => teamData.value?.id ?? 0)
// 自己名下（只有自己的那个团队）不说「团队」：说到归属的几句各有一份。
const scope = computed(() => (teamData.value?.personal ? 'own' : 'team'))

const devices = ref<MyDevice[]>([])
const myDevices = ref<MyDevice[]>([])
const loading = ref(false)
/** 写操作失败：一句话，可关，页面照旧（下面那块内容没受影响）。 */
const error = ref<string | null>(null)
/** 读失败：这一块内容根本没拿到，`devices` 空是假的。 */
const loadError = ref<unknown>(null)
const loadFailed = computed(() => loadError.value !== null)
const busy = ref<string | null>(null)

const myDeviceIds = computed(() => new Set(myDevices.value.map((device) => device.device_id)))
const addable = computed(() => myDevices.value.filter((device) => !device.team_ids.includes(teamId.value)))
const onlineCount = computed(() => devices.value.filter((device) => device.online).length)

function errorMessage(value: unknown, fallback: string): string {
  return value instanceof Error ? value.message : fallback
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [teamDevices, mine] = await Promise.all([
      listTeamDevices(teamId.value),
      listMyDevices().catch(() => ({ devices: [] })),
    ])
    devices.value = teamDevices.devices
    myDevices.value = mine.devices
  } catch (cause) {
    // 原来记在 `error` 里 —— 那条提示可关，关掉之后屏幕上只剩「还没有自己的机器」。
    loadError.value = cause
  } finally {
    loading.value = false
  }
}

async function addMachine(device: MyDevice) {
  busy.value = device.device_id
  error.value = null
  try {
    await registerDeviceForTeam(device.device_id, teamId.value)
    await load()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.addMachineFailed'))
  } finally {
    busy.value = null
  }
}

async function removeMachine(device: MyDevice) {
  const ok = await confirm(t(`teams.compute.${scope.value}.removeDeviceConfirm`, { name: device.name }), {
    danger: true,
    confirmLabel: t(`teams.compute.${scope.value}.remove`),
  }).wait()
  if (!ok) return
  busy.value = device.device_id
  error.value = null
  try {
    await unregisterDeviceFromTeam(device.device_id, teamId.value)
    await load()
  } catch (cause) {
    error.value = errorMessage(cause, t('teams.compute.removeMachineFailed'))
  } finally {
    busy.value = null
  }
}

onMounted(load)
watch(teamId, load)
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
      :error="loadFailureReason(loadError)"
      :forbidden="isForbidden(loadError)"
      @retry="load"
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
                @click="addMachine(device)"
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
                  <i18n-t keypath="work.deviceInUse.line" tag="span">
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
                  <BaseButton size="sm" :loading="busy === device.device_id" @click="removeMachine(device)">
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
