<script setup lang="ts">
// 团队算力 (execution-architecture v4): the machines registered for this team
// (为团队注册设备). Every project of the team may run turns on these — a topic picks
// 自托管设备 in its composer to use one. This page owns the 归属 layer: 「加机器」
// registers one of MY enrolled machines to THIS team (the personal team included —
// 个人 = 单人真团队). Enrollment itself (认证 layer) lives in 「我的设备」.
import type { MyDevice } from '@/cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { listMyDevices, listTeamDevices, registerDeviceForTeam, unregisterDeviceFromTeam } from '@/api'

const route = useRoute()
const teamId = computed(() => Number(route.params.teamId))

const devices = ref<MyDevice[]>([]) // the team's roster (all members' machines)
const myDevices = ref<MyDevice[]>([]) // my enrolled machines (认证 layer)
const loading = ref(false)
const error = ref<string | null>(null)
const busy = ref<string | null>(null) // device_id with a register/remove in flight

const onlineCount = computed(() => devices.value.filter((d) => d.online).length)
const myDeviceIds = computed(() => new Set(myDevices.value.map((d) => d.device_id)))
// My machines not yet registered to this team — the 「加机器」 menu.
const addable = computed(() => myDevices.value.filter((d) => !d.team_ids.includes(teamId.value)))

async function load() {
  loading.value = true
  error.value = null
  try {
    devices.value = (await listTeamDevices(teamId.value)).devices
    // Best-effort: without enrolled machines the connector may 401 — the roster
    // still renders, only the 「加机器」 menu falls back to its empty hint.
    myDevices.value = await listMyDevices()
      .then((r) => r.devices)
      .catch(() => [])
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载团队算力失败'
  } finally {
    loading.value = false
  }
}

async function addMachine(d: MyDevice) {
  busy.value = d.device_id
  try {
    await registerDeviceForTeam(d.device_id, teamId.value)
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加机器失败'
  } finally {
    busy.value = null
  }
}

async function removeMachine(d: MyDevice) {
  busy.value = d.device_id
  try {
    await unregisterDeviceFromTeam(d.device_id, teamId.value)
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '移出失败'
  } finally {
    busy.value = null
  }
}

onMounted(load)
watch(teamId, load)
</script>

<template>
  <v-container class="px-6 py-4" fluid>
    <div class="mb-4 d-flex align-start">
      <div>
        <h2 class="text-h6 font-weight-medium mb-1">算力</h2>
        <p class="text-body-2 text-medium-emphasis mb-0">
          注册给这个小队的机器。小队里任何项目的话题都可以选「自托管设备」跑在这些机器上， 工作树与数据留在本地。
        </p>
      </div>
      <v-spacer />
      <v-menu location="bottom end">
        <template #activator="{ props: menuProps }">
          <v-btn v-bind="menuProps" color="primary" variant="flat" prepend-icon="mdi-plus"> 加机器 </v-btn>
        </template>
        <v-list density="compact" min-width="260">
          <v-list-item
            v-for="d in addable"
            :key="d.device_id"
            :title="d.name"
            :disabled="busy === d.device_id"
            @click="addMachine(d)"
          >
            <template #prepend>
              <v-icon :color="d.online ? 'success' : 'grey'" size="12" class="mr-1">mdi-circle</v-icon>
            </template>
          </v-list-item>
          <v-list-item v-if="!addable.length && myDevices.length" disabled>
            <span class="text-caption text-medium-emphasis">你的机器都已注册给这个小队</span>
          </v-list-item>
          <v-list-item v-if="!myDevices.length" :to="{ name: 'my-devices' }" title="先去「我的设备」接入机器">
            <template #prepend>
              <v-icon size="16" class="mr-1">mdi-laptop-account</v-icon>
            </template>
          </v-list-item>
        </v-list>
      </v-menu>
    </div>

    <div v-if="loading" class="py-10 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>

    <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="error = null">
      {{ error }}
    </v-alert>

    <div v-else-if="devices.length === 0" class="empty-state text-center py-12">
      <v-icon icon="mdi-server-network-off" size="56" color="grey-lighten-2" class="mb-3" />
      <h3 class="text-subtitle-1 font-weight-medium mb-1">这个小队还没有算力</h3>
      <p class="text-body-2 text-medium-emphasis mb-0">点右上角「加机器」，把你已接入的机器注册给这个小队。</p>
    </div>

    <template v-else>
      <div class="text-caption text-medium-emphasis mb-3">{{ devices.length }} 台机器 · {{ onlineCount }} 台在线</div>
      <v-row>
        <v-col v-for="d in devices" :key="d.device_id" cols="12" sm="6" lg="4">
          <v-card variant="outlined" rounded="lg" class="pa-4 fill-height">
            <div class="d-flex align-center mb-2">
              <v-icon :color="d.online ? 'success' : 'grey'" size="20" class="mr-2"> mdi-laptop </v-icon>
              <span class="text-subtitle-2 font-weight-medium text-truncate">
                {{ d.name }}
              </span>
              <v-spacer />
              <span class="text-caption" :class="d.online ? 'text-success font-weight-medium' : 'text-medium-emphasis'">
                <span class="status-dot" :class="d.online ? 'status-dot--on' : ''" />
                {{ d.online ? '在线' : '离线' }}
              </span>
            </div>
            <div class="text-caption text-medium-emphasis" style="font-family: monospace">
              {{ d.device_id }}
            </div>
            <div v-if="d.screens.length" class="mt-3 d-flex flex-wrap ga-2">
              <v-chip v-for="s in d.screens" :key="s.sid" size="x-small" variant="tonal" color="primary">
                <v-icon start size="12">mdi-monitor-eye</v-icon>
                运行中 · @{{ s.agent_handle }}
              </v-chip>
            </div>
            <!-- Only the machine's owner may pull it out of the team. -->
            <div v-if="myDeviceIds.has(d.device_id)" class="mt-2 d-flex justify-end">
              <v-btn
                size="x-small"
                variant="text"
                color="error"
                :loading="busy === d.device_id"
                @click="removeMachine(d)"
              >
                移出小队
              </v-btn>
            </div>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </v-container>
</template>

<style scoped>
.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-right: 4px;
  background: rgb(var(--v-theme-on-surface));
  opacity: 0.35;
}
.status-dot--on {
  background: rgb(var(--v-theme-success));
  opacity: 1;
}
</style>
