<script setup lang="ts">
// 方案 B of 提交入口: in a project claimed from a 题目, the center column's
// header (over the channel or over 项目文件) carries one quiet chip, the
// breadcrumb back to that 题目: 「ICS 2026 › Data Lab · 截止 11月14日」 and,
// in small text, where my (or my team's) submission stands. A click opens the
// 题目 in the space, where submitting happens. In any other project, or under
// another 方案, it draws nothing.
import { computed } from 'vue'

import { topicOf } from './course'
import { isClaim, SPACE } from './data'
import { myStatus, S, showTopic } from './state'
import { V } from './variants'

import { monthDay } from '@/lib/dateText'

const origin = computed(() => (V.submit === 'B' && isClaim(S.project) ? topicOf(S.project) : null))
const mine = computed(() => (origin.value ? myStatus(origin.value.id) : null))
</script>

<template>
  <button
    v-if="origin && mine"
    type="button"
    class="ot-crumb"
    data-origin-crumb
    :title="`来自 ${SPACE.name} · ${origin.title}：在空间里打开`"
    @click="showTopic(origin.id)"
  >
    <span class="ot-crumb__space">{{ SPACE.brief }}</span>
    <v-icon size="12" icon="mdi-chevron-right" />
    <span class="ot-crumb__topic">{{ origin.title }}</span>
    <span class="ot-crumb__due">· 截止 {{ monthDay(Date.parse(origin.due)) }}</span>
    <span class="ot-crumb__st" :class="`is-${mine.tone}`">{{ mine.text }}</span>
  </button>
</template>

<style scoped>
.ot-crumb {
  display: inline-flex;
  flex: 0 1 auto;
  gap: 4px;
  align-items: center;
  min-width: 0;
  height: 24px;
  padding: 0 8px;
  overflow: hidden;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  cursor: pointer;
  background: var(--fill);
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-crumb:hover {
  color: var(--text);
  background: var(--fill-2);
}

.ot-crumb .v-icon {
  flex: none;
}

.ot-crumb__topic {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
}

.ot-crumb__due,
.ot-crumb__space {
  flex: none;
}

/* Where my submission stands: small, after a hairline, green only once it is through. */
.ot-crumb__st {
  flex: none;
  padding-left: 6px;
  margin-left: 2px;
  border-left: 1px solid var(--line-2);
}

.ot-crumb__st.is-ok {
  color: var(--ok-ink);
}
</style>
