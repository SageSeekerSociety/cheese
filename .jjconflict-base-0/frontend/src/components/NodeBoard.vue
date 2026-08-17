<script setup lang="ts">
import type { MarketNodes } from '../cx_types'

import { onBeforeUnmount, onMounted, ref } from 'vue'

import { getMarketNodes } from '../api'

// 节点状态 (spec §9.1): the physical side of the compute pools — every
// configured node (local docker + cheesed remote), its liveness, and how many
// turns it is running right now. Self-contained: fetches + refreshes itself.
const board = ref<MarketNodes | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
let timer: number | undefined

async function load() {
  loading.value = board.value === null
  error.value = null
  try {
    board.value = await getMarketNodes()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载机器状态失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
  // Light auto-refresh so 在线/负载 stays honest while the page is open.
  timer = window.setInterval(() => void load(), 15000)
})
onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <div class="node-board">
    <div class="node-board__head">
      <v-icon size="20" class="me-2 c-muted">mdi-lan</v-icon>
      <h2 class="node-board__title">机器状态</h2>
      <span class="node-board__hint c-faint">算力池背后的机器，在线状态与当前负载</span>
      <v-spacer />
      <span v-if="board" class="node-board__total c-faint"> 全平台进行中 {{ board.active_turns_total }} 轮 </span>
    </div>

    <div v-if="loading" class="d-flex justify-center py-6">
      <v-progress-circular indeterminate color="primary" size="24" />
    </div>
    <v-alert v-else-if="error" type="error" density="comfortable">
      {{ error }}
    </v-alert>

    <div v-else-if="board" class="node-grid">
      <article v-for="n in board.nodes" :key="n.id" class="node-card" :class="{ 'node-card--offline': !n.online }">
        <div class="node-card__top">
          <span class="node-dot" :class="n.online ? 'node-dot--on' : 'node-dot--off'" />
          <span class="node-card__status">{{ n.online ? '在线' : '离线' }}</span>
          <span class="node-card__kind">{{ n.kind === 'local' ? '本地' : '远程' }}</span>
          <span v-if="n.current" class="node-card__current">当前执行的机器</span>
        </div>
        <h3 class="node-card__title">{{ n.label }}</h3>
        <p class="node-card__desc c-muted">{{ n.description }}</p>
        <div class="node-card__meta">
          <span class="node-card__load">
            <v-icon size="13">mdi-pulse</v-icon>
            进行中 {{ n.active_turns }} 轮
          </span>
          <span class="node-card__detail c-faint">{{ n.detail }}</span>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.node-board {
  margin-bottom: 34px;
}
.node-board__head {
  display: flex;
  align-items: baseline;
  margin-bottom: 14px;
}
.node-board__title {
  font-size: 1.05rem;
  font-weight: 600;
}
.node-board__hint {
  margin-left: 10px;
  font-size: 0.8rem;
}
.node-board__total {
  font-size: 0.78rem;
  font-variant-numeric: tabular-nums;
}
.node-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}
.node-card {
  border: 1px solid rgba(var(--v-border-color), 0.55);
  border-radius: 12px;
  padding: 16px;
  background: var(--surface);
  display: flex;
  flex-direction: column;
}
.node-card--offline {
  opacity: 0.68;
}
.node-card__top {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}
.node-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}
.node-dot--on {
  background: var(--ok);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 18%, transparent);
}
.node-dot--off {
  background: var(--faint);
}
.node-card__status {
  font-size: 0.76rem;
  font-weight: 500;
  color: var(--muted);
}
.node-card__kind {
  font-size: 0.68rem;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.1);
  padding: 1px 8px;
  border-radius: var(--radius-lg);
}
.node-card__current {
  font-size: 0.66rem;
  color: var(--muted);
  border: 1px solid rgba(var(--v-border-color), 0.7);
  padding: 0 6px;
  border-radius: var(--radius-lg);
}
.node-card__title {
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.3;
  margin-bottom: 4px;
}
.node-card__desc {
  font-size: 0.82rem;
  line-height: 1.55;
  flex: 1;
}
.node-card__meta {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.node-card__load {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.78rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.node-card__detail {
  font-size: 0.74rem;
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
</style>
