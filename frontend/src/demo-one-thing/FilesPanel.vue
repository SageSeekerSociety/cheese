<script setup lang="ts">
// The right panel beside 项目文件: 待定改动 (芝士's suggestions waiting on a
// person, accepted with one click) and 历史 (every change that took effect,
// the accepted task included, each one undoable).
import type { PanelTab } from '@/components/panels/PanelTabs.vue'

import { computed } from 'vue'

import { MINUTES_DOC, NAMES } from './data'
import { S, suggAccept, suggReject, undo } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import DocSuggestionCard from '@/components/panels/doc/DocSuggestionCard.vue'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import { relTime } from '@/lib/relTime'

const pending = computed(() => (S.suggAccepted || S.suggRejected ? 0 : 1))
const tabs = computed<PanelTab[]>(() => [
  {
    key: 'pending',
    label: '待定改动',
    icon: 'mdi-file-document-edit-outline',
    signal: pending.value ? { kind: 'count', count: pending.value, fresh: true } : undefined,
  },
  { key: 'history', label: '历史', icon: 'mdi-history' },
])
</script>

<template>
  <aside class="ot-fp" data-zone="right">
    <header class="ot-fp__head">
      <span class="ot-fp__name">{{ S.file.split('/').pop() }}</span>
      <span class="ot-fp__dir">{{ S.file.includes('/') ? S.file.slice(0, S.file.lastIndexOf('/')) : '' }}</span>
    </header>
    <PanelTabs :tabs="tabs" :active="S.ftab" @select="S.ftab = $event" />
    <div class="ot-fp__body">
      <template v-if="S.ftab === 'pending'">
        <template v-if="pending">
          <p v-if="S.file !== MINUTES_DOC" class="ot-fp__other">这个文件没有待定的改动。其他文件：</p>
          <div class="ot-fp__card" data-zone="suggestion">
            <DocSuggestionCard
              author-name="芝士"
              author-handle="cheese"
              author-agent
              author-avatar=""
              :index="0"
              :total="1"
              reason="《组会纪要》第 3 条：「（待补）」改成「下周组会前把 COCO 5K 的结果更新到这里。」"
              editable
              @accept="suggAccept"
              @reject="suggReject"
            />
          </div>
        </template>
        <BaseEmptyState v-else size="compact" icon="mdi-check-all" title="暂无待定的改动" />
      </template>
      <ol v-else class="ot-hist">
        <li v-for="(h, i) in S.history" :key="h.what + h.at" class="ot-hist__row" :class="{ 'ot-flash': h.fresh }">
          <CheeseAvatar v-if="h.who === 'cheese'" :size="22" name="芝士" handle="cheese" />
          <UserAvatar v-else :size="22" :name="NAMES[h.who]" :seed="h.who" />
          <div class="ot-hist__text">
            <span
              ><b>{{ NAMES[h.who] }}</b> {{ h.what }}</span
            >
            <span class="t-meta-read" :title="new Date(h.at).toLocaleString()">{{ relTime(h.at) }}</span>
          </div>
          <BaseButton class="ot-hist__undo" kind="ghost" size="sm" @click="undo(i)">撤回</BaseButton>
        </li>
      </ol>
    </div>
  </aside>
</template>

<style scoped>
.ot-fp {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-fp__head {
  display: flex;
  align-items: center;
  height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid var(--line);
}

.ot-fp__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.ot-fp__dir {
  margin-left: 8px;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-fp__other {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--muted);
}

.ot-hist__row:hover {
  background: var(--fill);
}

.ot-hist__undo {
  opacity: 0;
}

.ot-hist__row:hover .ot-hist__undo,
.ot-hist__undo:focus-visible {
  opacity: 1;
}

.ot-fp__body {
  flex: 1;
  min-height: 0;
  padding: 16px;
  overflow: auto;
}

.ot-fp__card {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ot-fp__hint {
  margin: 12px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.ot-hist {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.ot-hist__row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 8px;
  border-radius: var(--radius-md);
}

.ot-hist__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ot-hist__text b {
  color: var(--ink);
}
</style>
