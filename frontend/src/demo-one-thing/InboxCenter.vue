<script setup lang="ts">
// 需要我的: every 一件事 that concerns me, the ones waiting on me first. In the
// course: the 题目 I have not passed yet, the nearest deadline first.
import { computed } from 'vue'

import { dueChip, PROBLEMS, standing } from './course'
import { CHANNELS } from './data'
import { ownerName, taskLine } from './shapes'
import { openProblem, openThing, S } from './state'

import TaskCard from '@/components/room/TaskCard.vue'
import { monthDayTime } from '@/lib/dateText'

const GROUPS = [
  { status: 'wait', label: '等你' },
  { status: 'run', label: '在做' },
  { status: 'talk', label: '讨论中' },
  { status: 'done', label: '最近完成' },
] as const

const groups = computed(() =>
  GROUPS.map((g) => ({ ...g, things: S.things.filter((t) => t.status === g.status) })).filter((g) => g.things.length)
)

const chanName = (id: string) => CHANNELS.find((c) => c.id === id)?.title ?? ''

const course = computed(() => S.project === 'course')
const todo = computed(() =>
  PROBLEMS.filter((p) => standing(S.subs[p.id]) !== 'passed').sort((a, b) => Date.parse(a.due) - Date.parse(b.due))
)
</script>

<template>
  <section class="ot-inbox" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">需要我的</h2>
      <span class="ot-head__sub">{{ course ? todo.length : S.things.length }} 件</span>
    </header>
    <div v-if="course" class="ot-inbox__body">
      <section class="ot-inbox__group">
        <h3 class="t-eyebrow-read ot-inbox__label">还没通过的题目 · {{ todo.length }}</h3>
        <button v-for="p in todo" :key="p.id" type="button" class="ot-inbox__prob" @click="openProblem(p.id)">
          <v-icon size="16" :icon="p.kind === 'gdb' ? 'mdi-bomb' : 'mdi-code-braces'" />
          <span class="ot-inbox__ptitle">{{ p.title }}</span>
          <span v-if="dueChip(p.due)" class="ot-inbox__chip">{{ dueChip(p.due) }}</span>
          <span class="ot-inbox__due">截止 {{ monthDayTime(Date.parse(p.due)) }}</span>
        </button>
      </section>
    </div>
    <div v-else class="ot-inbox__body">
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
