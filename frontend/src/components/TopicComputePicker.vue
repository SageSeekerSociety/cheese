<script setup lang="ts">
// 会话级算力选择 (execution-architecture v4): each topic runs its turns on a
// compute pool it picks BEFORE its first turn. The choice sticks as the project's
// default (so the next new topic inherits it) and FREEZES once the topic has run —
// its work tree + session live on that compute and must not move. This chip lives
// in the composer toolbar; it shows the effective pool and, while unlocked, lets
// the user switch pools.
import type { PoolListing, TopicComputeProfile } from '../cx_types'

import { onMounted, ref, watch } from 'vue'

import { getTopicComputeProfile, setTopicComputeProfile } from '../api'

const props = defineProps<{ topicId: string }>()

const state = ref<TopicComputeProfile | null>(null)
const loading = ref(false)
const saving = ref<string | null>(null)
const error = ref<string | null>(null)
const menuOpen = ref(false)

// A glyph per pool so the chip reads at a glance — platform container vs. your own
// machine vs. GPU. Unknown ids fall back to a generic compute icon.
const POOL_ICON: Record<string, string> = {
  'local-docker': 'mdi-server',
  device: 'mdi-laptop',
  'remote-cheesed': 'mdi-server-network',
  gpu: 'mdi-expansion-card',
}
function iconFor(id: string): string {
  return POOL_ICON[id] ?? 'mdi-chip'
}

function labelFor(id: string): string {
  const hit = state.value?.profiles.find((p) => p.id === id)
  return hit?.label ?? id
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

async function pick(p: PoolListing) {
  if (!state.value || state.value.locked || !p.available) return
  if (state.value.current === p.id && !state.value.inherited) {
    menuOpen.value = false
    return
  }
  saving.value = p.id
  error.value = null
  try {
    const r = await setTopicComputeProfile(props.topicId, p.id)
    state.value = { ...state.value, current: r.current, inherited: false }
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
    <!-- Locked: the topic has run — the pin is frozen, no menu. -->
    <span v-if="state.locked" class="cp-chip cp-chip--locked" title="话题已开始，算力已锁定；新建话题可另选算力">
      <v-icon size="12">mdi-lock-outline</v-icon>
      <v-icon size="13">{{ iconFor(state.current) }}</v-icon>
      {{ labelFor(state.current) }}
    </span>

    <!-- Switchable: pick a pool for this session. -->
    <v-menu v-else v-model="menuOpen" location="top start" :close-on-content-click="false">
      <template #activator="{ props: menuProps }">
        <button type="button" class="cp-chip" v-bind="menuProps" :disabled="loading">
          <v-icon size="13">{{ iconFor(state.current) }}</v-icon>
          {{ labelFor(state.current) }}
          <span v-if="state.inherited" class="cp-inherit">· 跟随项目</span>
          <v-icon size="13" class="cp-caret">mdi-menu-up</v-icon>
        </button>
      </template>

      <v-card min-width="272" class="cp-menu">
        <div class="cp-menu__head">选择本话题的算力</div>
        <div class="cp-menu__hint">发出第一条消息后锁定，新建话题可再选。</div>
        <button
          v-for="p in state.profiles"
          :key="p.id"
          type="button"
          class="cp-row"
          :class="{
            'cp-row--active': state.current === p.id && !state.inherited,
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
            <div v-if="!p.available" class="cp-row__off">尚未接入，暂不可选</div>
          </div>
          <v-progress-circular v-if="saving === p.id" indeterminate size="15" width="2" />
          <v-icon v-else-if="state.current === p.id && !state.inherited" size="17" color="primary"> mdi-check </v-icon>
        </button>
        <div v-if="error" class="cp-menu__err">{{ error }}</div>
      </v-card>
    </v-menu>
  </div>
</template>

<style scoped>
.compute-picker {
  display: inline-flex;
}
.cp-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 8px;
  border: 1px solid rgba(var(--v-border-color), 0.16);
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.03);
  color: rgb(var(--v-theme-on-surface));
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition:
    background 0.12s ease,
    border-color 0.12s ease;
}
.cp-chip:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-color: rgba(var(--v-border-color), 0.28);
}
.cp-chip--locked {
  cursor: default;
  opacity: 0.72;
}
.cp-chip--locked:hover {
  background: rgba(var(--v-theme-on-surface), 0.03);
  border-color: rgba(var(--v-border-color), 0.16);
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
