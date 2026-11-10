<script setup lang="ts">
// The right panel beside a 题目: the 题目 is the one selected thing. 题面 is
// the statement, 评测记录 my submissions (newest first, each one opens to its
// tests), 问芝士 a conversation with 芝士 as a tutor: the course is set to
// 「芝士只给提示，不给答案」, and that setting is shown where it applies.
import type { PanelTab } from '@/components/panels/PanelTabs.vue'

import { computed, ref, watch } from 'vue'

import { problemById } from './course'
import DemoComposer from './DemoComposer.vue'
import DemoTimeline from './DemoTimeline.vue'
import JudgeResult from './JudgeResult.vue'
import { whenText } from './shapes'
import { askTutor, go, S } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import { monthDayTime } from '@/lib/dateText'

const p = computed(() => problemById(S.problem)!)
const subs = computed(() => S.subs[p.value.id] ?? [])
const ask = computed(() => S.extra[`ask:${p.value.id}`] ?? [])

const tabs = computed<PanelTab[]>(() => [
  { key: 'statement', label: '题面', icon: 'mdi-text-box-outline' },
  {
    key: 'subs',
    label: '评测记录',
    icon: 'mdi-history',
    signal: subs.value.length ? { kind: 'count', count: subs.value.length } : undefined,
  },
  { key: 'ask', label: '问芝士', icon: 'mdi-message-outline' },
])

/** The submission opened to its tests; the newest one by default. */
const openSub = ref<string | null>(null)
const opened = (id: string, i: number) => (openSub.value === null ? i === 0 : openSub.value === id)
watch(
  () => [S.problem, subs.value.length],
  () => (openSub.value = null)
)
</script>

<template>
  <aside class="ot-pp" data-zone="right">
    <header class="ot-pp__head">
      <v-icon size="18" class="ot-pp__icon" :icon="p.kind === 'gdb' ? 'mdi-bomb' : 'mdi-code-braces'" />
      <h2 class="ot-pp__title" :title="p.title">{{ p.title }}</h2>
      <span class="ot-head__gap" />
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-close"
        title="关闭"
        aria-label="关闭"
        @click="go('chan', { chan: S.chan, thing: null })"
      />
    </header>
    <PanelTabs :tabs="tabs" :active="S.ptab" @select="S.ptab = $event" />

    <div class="ot-pp__body">
      <div v-if="S.ptab === 'statement'" class="ot-pp__pad">
        <p class="ot-pp__meta">截止 {{ monthDayTime(Date.parse(p.due)) }} · 以截止前最后一次提交为准</p>
        <MarkdownView class="md-content" :source="p.statement" />
      </div>

      <div v-else-if="S.ptab === 'subs'" class="ot-pp__pad">
        <BaseEmptyState
          v-if="!subs.length"
          size="compact"
          icon="mdi-history"
          title="还没有提交"
          desc="写好之后点「提交评测」，每次的结果都记在这里"
        />
        <div v-for="(s, i) in subs" :key="s.id" class="ot-pp__sub" :class="{ 'is-open': opened(s.id, i) }">
          <button type="button" class="ot-pp__subrow" @click="openSub = opened(s.id, i) ? '' : s.id">
            <span class="ot-pp__n">#{{ subs.length - i }}</span>
            <b class="ot-pp__score" :class="{ 'is-ok': s.score === s.total }">{{ s.score }}/{{ s.total }}</b>
            <span class="ot-pp__verdict">{{ s.score === s.total ? '已通过' : '没有全部通过' }}</span>
            <span class="ot-head__gap" />
            <span class="ot-pp__at">{{ whenText(s.at) }}</span>
            <v-icon size="16" :icon="opened(s.id, i) ? 'mdi-chevron-up' : 'mdi-chevron-down'" />
          </button>
          <JudgeResult v-if="opened(s.id, i)" :sub="s" class="ot-pp__detail" />
        </div>
      </div>

      <template v-else>
        <p class="ot-pp__rule"><v-icon size="14" icon="mdi-school-outline" />课程设置：芝士只给提示，不给答案</p>
        <div class="ot-pp__chat">
          <DemoTimeline v-if="ask.length" :lines="ask" :conversation="`ask:${p.id}`" />
          <BaseEmptyState
            v-else
            size="compact"
            icon="mdi-message-outline"
            title="问芝士这道题的思路"
            desc="它会给提示、指出该看哪里，不会给出答案"
          />
        </div>
      </template>
    </div>

    <DemoComposer
      v-if="S.ptab === 'ask'"
      v-model="S.askTyping"
      placeholder="问芝士这道题，它只给提示"
      @send="askTutor"
    />
  </aside>
</template>

<style scoped>
.ot-pp {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-pp__head {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  height: 48px;
  padding: 0 8px 0 16px;
  border-bottom: 1px solid var(--line);
}

.ot-pp__icon {
  flex: none;
  color: var(--muted);
}

.ot-pp__title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ot-pp__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: auto;
}

.ot-pp__pad {
  padding: 12px 16px;
}

.ot-pp__meta {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-pp__sub {
  border-bottom: 1px solid var(--line);
}

.ot-pp__subrow {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  height: 40px;
  padding: 0 4px;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-pp__subrow:hover {
  background: var(--fill);
}

.ot-pp__subrow .v-icon {
  color: var(--muted);
}

.ot-pp__n {
  width: 24px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}

.ot-pp__score {
  font-family: var(--font-mono);
  color: var(--ink);
}

.ot-pp__score.is-ok {
  color: var(--ok-ink);
}

.ot-pp__at {
  font-size: 12px;
  color: var(--muted);
}

.ot-pp__detail {
  padding: 4px 4px 12px 36px;
}

.ot-pp__rule {
  display: flex;
  flex: none;
  gap: 6px;
  align-items: center;
  margin: 0;
  padding: 8px 16px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}

.ot-pp__chat {
  flex: 1;
  min-height: 0;
  padding: 8px 0;
  overflow: auto;
}
</style>
