<script setup lang="ts">
// 项目总览最上面的「现状」：项目分成的几块，每块的状态和出处。状态和出处都是后端按
// 记录核对过的（`project_situation.card`）；这一块只画，出处点开去那个任务或项目目标。
import type { SituationPart, SituationSource } from '@/api/projectProgress'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

defineProps<{
  /** 上一次整理出的那几块；还没写成过是空的。 */
  parts: SituationPart[]
  writtenAt: string | null
  /** 后台正在重写。 */
  writing: boolean
  agentName: string
  agentHandle: string | null
}>()

const emit = defineEmits<{
  'open-task': [task: { taskId: string; roomId: string }]
  'open-goals': []
}>()

/** 文档里状态标签的三种样子（`.doc-status`）。 */
const KIND = { usable: 'ok', underway: 'warn', not_started: 'no' } as const
const LABEL = {
  usable: 'work.overview.situationUsable',
  underway: 'work.overview.situationUnderway',
  not_started: 'work.overview.situationNotStarted',
} as const

function label(source: SituationSource): string {
  return source.kind === 'goal' ? t('work.overview.situationGoal') : source.title
}

function open(source: SituationSource) {
  if (source.kind === 'goal') emit('open-goals')
  else if (source.task_id && source.room_id) emit('open-task', { taskId: source.task_id, roomId: source.room_id })
}
</script>

<template>
  <section class="situation" data-testid="overview-situation" :aria-busy="writing">
    <div class="situation__head">
      <CheeseAvatar :size="20" :name="agentName" :handle="agentHandle" />
      <h2 class="situation__title">{{ t('work.overview.situation') }}</h2>
      <span class="situation__when t-meta c-muted">
        <template v-if="writing">{{ t('work.overview.situationWriting', { agent: agentName }) }}</template>
        <template v-else-if="writtenAt">
          {{ t('work.overview.situationWritten', { agent: agentName, when: relTime(writtenAt) }) }}
        </template>
      </span>
    </div>
    <ul v-if="parts.length" class="situation__parts">
      <li v-for="(part, i) in parts" :key="i" class="situation__part">
        <div class="situation__line">
          <span class="situation__name">{{ part.name }}</span>
          <span class="doc-status" :data-status="KIND[part.status]">{{ t(LABEL[part.status]) }}</span>
          <span class="situation__text" data-user-content>{{ part.text }}</span>
        </div>
        <div class="situation__sources" :aria-label="t('work.overview.situationSources')">
          <button
            v-for="(source, j) in part.sources"
            :key="j"
            type="button"
            class="situation__source"
            :title="source.kind === 'goal' ? source.quote : source.title"
            @click="open(source)"
          >
            {{ label(source) }}
          </button>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.situation {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px 24px 18px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.situation__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.situation__title {
  flex: 1 1 auto;
  margin: 0;
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  color: var(--ink);
}
.situation__when {
  flex: none;
}
.situation__parts {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.situation__line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 8px;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
}
.situation__name {
  font-weight: 600;
  color: var(--ink);
}
.situation__sources {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 12px;
  margin-top: 2px;
}
.situation__source {
  position: relative;
  max-width: 16em;
  overflow: hidden;
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  font-family: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.situation__source:hover {
  color: var(--text);
  text-decoration: underline;
}
</style>
