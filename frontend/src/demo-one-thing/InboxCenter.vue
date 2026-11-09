<script setup lang="ts">
// 需要我的: every 一件事 that concerns me, the ones waiting on me first.
import { computed } from 'vue'

import { CHANNELS } from './data'
import { ownerName, taskLine } from './shapes'
import { openThing, S } from './state'

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

const chanName = (id: string) => CHANNELS.find((c) => c.id === id)?.title ?? ''
</script>

<template>
  <section class="ot-inbox" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">需要我的</h2>
      <span class="ot-head__sub">{{ S.things.length }} 件</span>
    </header>
    <div class="ot-inbox__body">
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

.ot-inbox__chan {
  display: block;
  margin-bottom: 4px;
}
</style>
