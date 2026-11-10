<script setup lang="ts">
// 需要我的: the same page in every project. On top, the deadlines across all my
// projects and the course (a 题目 not claimed yet, not submitted, or a 题单
// with problems still open), nearest first; then every 一件事 that concerns
// me, the ones waiting on me first. A deadline opens its project.
import { computed } from 'vue'

import { CHANNELS } from './data'
import { ownerName, taskLine } from './shapes'
import { deadlines, openThing, S } from './state'

import TaskCard from '@/components/room/TaskCard.vue'

const GROUPS = [
  { status: 'wait', label: '等你' },
  { status: 'run', label: '在做' },
  { status: 'talk', label: '讨论中' },
  { status: 'done', label: '最近完成' },
] as const

const groups = computed(() =>
  GROUPS.map((g) => ({ ...g, things: S.things.filter((t) => t.status === g.status) })).filter((g) => g.things.length)
)
const dues = computed(() => deadlines())
const chanName = (id: string) => CHANNELS.find((c) => c.id === id)?.title ?? ''
</script>

<template>
  <section class="ot-inbox" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">需要我的</h2>
      <span class="ot-head__sub">{{ S.things.length + dues.length }} 件</span>
    </header>
    <div class="ot-inbox__body">
      <section v-if="dues.length" class="ot-inbox__group" data-zone="deadlines">
        <h3 class="t-eyebrow-read ot-inbox__label">截止 · {{ dues.length }}</h3>
        <button v-for="d in dues" :key="d.id" type="button" class="ot-inbox__prob" :data-due="d.id" @click="d.open()">
          <v-icon size="16" icon="mdi-calendar-clock-outline" />
          <span class="ot-inbox__ptitle">{{ d.title }}</span>
          <span class="ot-inbox__due">{{ d.sub }}</span>
          <span class="ot-inbox__chip">{{ d.chip }}</span>
        </button>
      </section>
      <section v-for="g in groups" :key="g.status" class="ot-inbox__group">
        <h3 class="t-eyebrow-read ot-inbox__label">{{ g.label }} · {{ g.things.length }}</h3>
        <div v-for="t in g.things" :key="t.id" class="ot-inbox__item">
          <span class="ot-inbox__chan t-meta-read"># {{ chanName(t.chan) }}</span>
          <TaskCard :task="taskLine(t)" :owner-name="ownerName(t)" @open="openThing" />
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.ot-inbox {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.ot-inbox__body {
  flex: 1;
  min-height: 0;
  padding: 16px 24px;
  overflow: auto;
}

.ot-inbox__group + .ot-inbox__group {
  margin-top: 24px;
}

.ot-inbox__label {
  margin: 0 0 8px;
}

.ot-inbox__item + .ot-inbox__item {
  margin-top: 12px;
}

.ot-inbox__prob {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  height: 40px;
  padding: 0 8px;
  font-size: 14px;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
}

.ot-inbox__prob:hover {
  background: var(--fill);
}

.ot-inbox__prob .v-icon,
.ot-inbox__due {
  color: var(--muted);
}

.ot-inbox__ptitle {
  flex: 1;
  min-width: 0;
}

.ot-inbox__due {
  font-size: 13px;
}

.ot-inbox__chip {
  padding: 0 6px;
  font-size: 12px;
  line-height: 20px;
  color: var(--accent-ink);
  background: var(--accent-wash);
  border-radius: var(--radius-sm);
}

.ot-inbox__chan {
  display: block;
  margin-bottom: 4px;
}
</style>
