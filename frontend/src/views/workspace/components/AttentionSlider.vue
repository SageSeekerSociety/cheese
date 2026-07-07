<!-- agent 在本群的「关注频率」三态滑块（替代成员行里的 agent 标签）。
     左→右：仅@ (MENTION) / 间隔 (INTERVAL) / 立即 (ALL)。
     划到中间(间隔)时，上方浮出一个气泡设置「两次关注间隔不短于 N 分钟（@除外）」。
     任何群成员都能调（与「谁都能给 agent 发消息、开现场」一致）。 -->
<template>
  <div class="att">
    <div v-if="mode === 'INTERVAL'" class="att-bubble" @click.stop>
      <span class="att-bubble__txt">间隔≥</span>
      <input
        class="att-bubble__min"
        type="number"
        min="1"
        :value="interval ?? 15"
        @change="onMinutes"
      />
      <span class="att-bubble__txt">分钟（@除外）</span>
    </div>
    <div class="att-slider" role="group" :title="modeTitle">
      <button
        v-for="s in STATES"
        :key="s.mode"
        type="button"
        class="att-seg"
        :class="{ 'att-seg--on': mode === s.mode }"
        :disabled="busy"
        :title="s.title"
        @click.stop="pick(s.mode)"
      >
        {{ s.label }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { AttentionMode } from '@/network/api/threads'

const props = defineProps<{ mode: AttentionMode; interval?: number; busy?: boolean }>()
const emit = defineEmits<{ (e: 'change', mode: AttentionMode, interval?: number): void }>()

const STATES: { mode: AttentionMode; label: string; title: string }[] = [
  { mode: 'MENTION', label: '仅@', title: '仅当被 @ 时处理' },
  { mode: 'INTERVAL', label: '间隔', title: '两次关注间隔不短于 N 分钟（@除外）' },
  { mode: 'ALL', label: '立即', title: '立即处理所有消息' },
]
const modeTitle = computed(() => STATES.find((s) => s.mode === props.mode)?.title ?? '')

function pick(mode: AttentionMode): void {
  if (mode === props.mode) return
  emit('change', mode, mode === 'INTERVAL' ? (props.interval ?? 15) : undefined)
}
function onMinutes(e: Event): void {
  const n = Math.max(1, Math.round(Number((e.target as HTMLInputElement).value) || 1))
  emit('change', 'INTERVAL', n)
}
</script>

<style scoped>
.att {
  position: relative;
  flex: none;
}
.att-slider {
  display: inline-flex;
  border-radius: 999px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  padding: 2px;
}
.att-seg {
  font-size: 11px;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 999px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  transition:
    background 0.15s,
    color 0.15s;
}
.att-seg--on {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}
.att-bubble {
  position: absolute;
  bottom: calc(100% + 6px);
  right: 0;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  padding: 5px 8px;
  border-radius: 8px;
  background: rgb(var(--v-theme-surface));
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.18);
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  font-size: 11px;
}
.att-bubble::after {
  /* little pointer */
  content: '';
  position: absolute;
  top: 100%;
  right: 16px;
  border: 5px solid transparent;
  border-top-color: rgb(var(--v-theme-surface));
}
.att-bubble__min {
  width: 40px;
  text-align: center;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 5px;
  padding: 1px 2px;
  font-size: 12px;
}
.att-bubble__txt {
  color: rgba(var(--v-theme-on-surface), 0.7);
}
</style>
