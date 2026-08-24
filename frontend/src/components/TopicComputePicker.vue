<script setup lang="ts">
// 会话级算力选择 (execution-architecture v4): each topic runs its turns on a
// compute pool it picks BEFORE its first turn. The choice sticks as the project's
// default (so the next new topic inherits it) and FREEZES once the topic has run —
// its work tree + session live on that compute and must not move. This chip lives
// in the composer toolbar; it shows the effective pool and, while unlocked, lets
// the user switch pools.
import type { PoolListing, TopicComputeProfile } from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'

import { getTopicComputeProfile, setTopicComputeProfile } from '../api'

const props = defineProps<{ topicId: string }>()

const state = ref<TopicComputeProfile | null>(null)
const loading = ref(false)
const saving = ref<string | null>(null)
const error = ref<string | null>(null)
const menuOpen = ref(false)
const DEVICE_PROFILE = 'device'

// Only pools that can actually be picked, plus whichever one this topic is
// already on. A permanently unavailable row teaches the reader that connecting
// something would light it up, so a pool that does not exist is not shown at
// all rather than greyed out. The current pool stays listed even when
// unselectable — a topic has to render a readable label for what it runs on.
const visibleProfiles = computed<PoolListing[]>(() => {
  const profiles = state.value?.profiles ?? []
  const current = state.value?.current
  const hasSelfHostedDevices = Boolean(state.value?.devices.length)
  return profiles.filter((p) => p.available || p.id === current || (p.id === DEVICE_PROFILE && hasSelfHostedDevices))
})

// A glyph per pool so the chip reads at a glance — the platform's own box vs.
// your machine vs. a leased one. Unknown ids fall back to a generic icon, which
// is also what a row from a future pool gets until it earns a glyph.
const POOL_ICON: Record<string, string> = {
  'tmux-hooks': 'mdi-server',
  device: 'mdi-laptop',
  cloud: 'mdi-cloud-outline',
}
function iconFor(id: string): string {
  return POOL_ICON[id] ?? 'mdi-chip'
}

function labelFor(id: string): string {
  const hit = state.value?.profiles.find((p) => p.id === id)
  return hit?.label ?? id
}

const currentLabel = computed(() => {
  if (state.value?.current !== DEVICE_PROFILE || !state.value.device_id) {
    return labelFor(state.value?.current ?? '')
  }
  return (
    state.value.devices.find((device) => device.device_id === state.value?.device_id)?.name ?? state.value.device_id
  )
})

function choiceKey(profileId: string, deviceId: string | null): string {
  return deviceId ? `${profileId}:${deviceId}` : profileId
}

function isSelected(profileId: string, deviceId: string | null): boolean {
  return state.value?.current === profileId && state.value.device_id === deviceId && !state.value.inherited
}

async function load() {
  loading.value = true
  error.value = null
  try {
    state.value = await getTopicComputeProfile(props.topicId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载算力失败'
  } finally {
    loading.value = false
  }
}

async function pick(p: PoolListing, deviceId: string | null = null) {
  if (!state.value || state.value.locked) return
  // An offline named machine is a valid deliberate choice: the topic waits for
  // that box. Only 「系统挑一台」 and ordinary pools obey p.available.
  if (!p.available && !(p.id === DEVICE_PROFILE && deviceId !== null)) return
  if (isSelected(p.id, deviceId)) {
    menuOpen.value = false
    return
  }
  saving.value = choiceKey(p.id, deviceId)
  error.value = null
  try {
    const r = await setTopicComputeProfile(props.topicId, p.id, deviceId)
    state.value = {
      ...state.value,
      current: r.current,
      device_id: r.device_id,
      inherited: false,
    }
    menuOpen.value = false
  } catch (e) {
    error.value = e instanceof Error ? e.message : '切换算力失败'
  } finally {
    saving.value = null
  }
}

onMounted(load)
watch(
  () => props.topicId,
  () => load()
)
</script>

<template>
  <div v-if="state" class="compute-picker">
    <!-- #358 Hosted Machine safety signal: this topic's agent runs BARE on the
         whole machine (operate its services, exec into other rooms, reach the
         internal network). A status-dot + neutral text (never a colored chip —
         product-ui.md), the honest #282 line as its tooltip, so whole-machine
         access is SEEN in the room, not silent (原则八). -->
    <span v-if="state.visibility?.machine_access" class="cp-machine" :title="state.visibility.notice">
      <span class="status-dot status-dot--warn" />
      整台机器
    </span>

    <!-- Locked: the topic has run — the pin is frozen, no menu. -->
    <span v-if="state.locked" class="cp-chip cp-chip--locked" title="话题已开始，算力已锁定；新建话题可另选算力">
      <v-icon size="12">mdi-lock-outline</v-icon>
      <v-icon size="13">{{ iconFor(state.current) }}</v-icon>
      {{ currentLabel }}
    </span>

    <!-- Switchable: pick a pool for this session. -->
    <v-menu v-else v-model="menuOpen" location="top start" :close-on-content-click="false">
      <template #activator="{ props: menuProps }">
        <button type="button" class="cp-chip" v-bind="menuProps" :disabled="loading">
          <v-icon size="13">{{ iconFor(state.current) }}</v-icon>
          {{ currentLabel }}
          <span v-if="state.inherited" class="cp-inherit">· 沿用上次</span>
          <v-icon size="13" class="cp-caret">mdi-menu-up</v-icon>
        </button>
      </template>

      <v-card min-width="272" class="cp-menu">
        <div class="cp-menu__head">选择本话题的算力</div>
        <div class="cp-menu__hint">发出第一条消息后锁定，新建话题可再选</div>
        <template v-for="p in visibleProfiles" :key="p.id">
          <div v-if="p.id === DEVICE_PROFILE" class="cp-group">
            <div class="cp-row cp-row--group">
              <v-icon size="17" class="cp-row__icon">{{ iconFor(p.id) }}</v-icon>
              <div class="cp-row__body">
                <div class="cp-row__label">
                  {{ p.label }}
                  <span class="cp-row__price">{{ p.price }}</span>
                </div>
                <div class="cp-row__desc">{{ p.description }}</div>
              </div>
            </div>

            <button
              type="button"
              class="cp-row cp-row--child"
              :class="{
                'cp-row--active': isSelected(p.id, null),
                'cp-row--off': !p.available,
              }"
              :disabled="!p.available || saving !== null"
              @click="pick(p, null)"
            >
              <v-icon size="16" class="cp-row__icon">mdi-auto-fix</v-icon>
              <div class="cp-row__body">
                <div class="cp-row__label">系统挑一台</div>
                <div class="cp-row__desc">首次运行时选择第一台在线健康的机器</div>
                <div v-if="!p.available" class="cp-row__off">没有在线机器</div>
              </div>
              <v-progress-circular v-if="saving === choiceKey(p.id, null)" indeterminate size="15" width="2" />
              <v-icon v-else-if="isSelected(p.id, null)" size="17" color="primary">mdi-check</v-icon>
            </button>

            <button
              v-for="device in state.devices"
              :key="device.device_id"
              type="button"
              class="cp-row cp-row--child"
              :class="{ 'cp-row--active': isSelected(p.id, device.device_id) }"
              :disabled="saving !== null"
              @click="pick(p, device.device_id)"
            >
              <v-icon size="16" class="cp-row__icon">mdi-laptop</v-icon>
              <div class="cp-row__body">
                <div class="cp-row__label">
                  {{ device.name }}
                  <span class="cp-device-status">
                    <span class="status-dot" :class="device.online ? 'status-dot--ok' : 'status-dot--muted'" />
                    {{ device.online ? '在线' : '离线' }}
                  </span>
                </div>
              </div>
              <v-progress-circular
                v-if="saving === choiceKey(p.id, device.device_id)"
                indeterminate
                size="15"
                width="2"
              />
              <v-icon v-else-if="isSelected(p.id, device.device_id)" size="17" color="primary"> mdi-check </v-icon>
            </button>
          </div>

          <button
            v-else
            type="button"
            class="cp-row"
            :class="{
              'cp-row--active': isSelected(p.id, null),
              'cp-row--off': !p.available,
            }"
            :disabled="!p.available || saving !== null"
            @click="pick(p)"
          >
            <v-icon size="17" class="cp-row__icon">{{ iconFor(p.id) }}</v-icon>
            <div class="cp-row__body">
              <div class="cp-row__label">
                {{ p.label }}
                <span class="cp-row__price">{{ p.price }}</span>
              </div>
              <div class="cp-row__desc">{{ p.description }}</div>
              <div v-if="!p.available" class="cp-row__off">暂未接入，不可选</div>
            </div>
            <v-progress-circular v-if="saving === choiceKey(p.id, null)" indeterminate size="15" width="2" />
            <v-icon v-else-if="isSelected(p.id, null)" size="17" color="primary">mdi-check</v-icon>
          </button>
        </template>
        <div v-if="error" class="cp-menu__err">{{ error }}</div>
      </v-card>
    </v-menu>
  </div>
</template>

<style scoped>
.compute-picker {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
/* Hosted Machine indicator — dot carries the warn signal, text stays neutral ink
   (product-ui.md: status is a dot + neutral text, not a colored chip). `help`
   cursor hints the tooltip that spells out the #282 access boundary. */
.cp-machine {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  line-height: 1;
  color: var(--text);
  cursor: help;
}
/* 它住在输入区的动作行里，那一行的规矩是：静止时谁也不画边框、不画底色，整行
   只有发送是实心的。描边 + 淡底的 chip 在那儿是第三种视觉语言，而算力只是一条
   设置，比动作还轻。高度跟着那一行走（28px），不自己定一个。 */
.cp-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.12s ease;
}
.cp-chip:hover {
  background: var(--fill);
}
.cp-chip--locked {
  cursor: default;
  opacity: 0.72;
}
.cp-chip--locked:hover {
  background: transparent;
}
.cp-inherit {
  color: rgba(var(--v-theme-on-surface), 0.5);
}
.cp-caret {
  margin-left: 1px;
  opacity: 0.6;
}
.cp-menu {
  padding: 6px;
}
.cp-menu__head {
  padding: 6px 8px 2px;
  font-size: 12px;
  font-weight: 600;
}
.cp-menu__hint {
  padding: 0 8px 6px;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.cp-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 8px;
  border-radius: 8px;
  text-align: left;
  background: transparent;
  cursor: pointer;
  transition: background 0.12s ease;
}
.cp-row:hover:not([disabled]) {
  background: rgba(var(--v-theme-on-surface), 0.05);
}
.cp-row--active {
  background: rgba(var(--v-theme-primary), 0.08);
}
.cp-row--group {
  cursor: default;
}
.cp-row--child {
  padding-left: 32px;
}
.cp-row--off {
  opacity: 0.5;
  cursor: not-allowed;
}
.cp-row__icon {
  margin-top: 1px;
  color: rgba(var(--v-theme-on-surface), 0.7);
}
.cp-row__body {
  flex: 1;
  min-width: 0;
}
.cp-row__label {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
}
.cp-row__price {
  font-size: 11px;
  font-weight: 400;
  color: rgba(var(--v-theme-on-surface), 0.55);
  white-space: nowrap;
}
.cp-device-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  font-weight: 400;
  color: rgba(var(--v-theme-on-surface), 0.55);
  white-space: nowrap;
}
.cp-row__desc {
  margin-top: 2px;
  font-size: 11px;
  line-height: 1.4;
  color: rgba(var(--v-theme-on-surface), 0.6);
}
.cp-row__off {
  margin-top: 2px;
  font-size: 11px;
  color: rgb(var(--v-theme-warning));
}
.cp-menu__err {
  padding: 4px 8px;
  font-size: 11px;
  color: rgb(var(--v-theme-error));
}
</style>
