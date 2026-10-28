<script setup lang="ts">
// 任务页顶上那一行：这个任务是从哪句话来的。引的是那句原话（谁说的、在哪说的、什么
// 时候），点它就回到那段对话、停在那一条上。自己新建的任务没有这句话，就照实说是谁
// 新建的（他自己的芝士替他建的，也照实说）；AI 队友建的而平台没记下它在回哪句话，也照实说
// 没记下，不拿别的话顶替。
import type { RoomTask } from '@/cx_types'
import type { TaskSource } from '@/types/taskOrigin'

import { computed } from 'vue'

import { t } from '@/i18n'
import { isAgentHandle } from '@/lib/authorship'
import { plainRefs } from '@/lib/refChip'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  /** 没取到（还在取，或后端还没有这一项）是 undefined：那时什么都不画。 */
  source: TaskSource | null | undefined
  task: RoomTask
  memberNames: Record<string, string>
}>()

const emit = defineEmits<{ (e: 'open', source: TaskSource): void }>()

function nameOf(handle: string): string {
  return props.memberNames[handle] || handle
}

const where = computed(() => {
  const source = props.source
  if (!source) return ''
  return t(`work.task.source.${source.where}`, { title: source.title })
})
// 引的那句话里 @ 过的人、提过的话题、指过的文件，读成名字，和对话里 chip 上的字一样。
const quote = computed(() => {
  const source = props.source
  if (!source) return ''
  const text = plainRefs(source.content, { mentionNames: props.memberNames, topicTitles: {}, notified: true })
  return t('work.task.source.quote', { name: nameOf(source.author), text })
})
const madeHere = computed(() => {
  const by = props.task.created_by
  if (!by) return t('work.task.source.unknown')
  if (props.task.created_by_assistant) return t('work.task.source.byAssistant', { name: nameOf(by) })
  return isAgentHandle(by)
    ? t('work.task.source.byAgent', { name: nameOf(by) })
    : t('work.task.source.byPerson', { name: nameOf(by) })
})
</script>

<template>
  <button v-if="source" type="button" class="task-source" data-testid="task-source" @click="emit('open', source)">
    <span class="t-meta c-faint">{{ where }} · {{ relTime(source.created_at) }}</span>
    <span class="t-body task-source__quote">{{ quote }}</span>
  </button>
  <div v-else-if="source === null" class="task-source task-source--none t-meta c-faint" data-testid="task-source">
    {{ madeHere }} · {{ relTime(task.created_at) }}
  </div>
</template>

<style scoped>
.task-source {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 10px 16px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: none;
  text-align: left;
}
button.task-source {
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
button.task-source:hover {
  background: var(--fill);
}
.task-source__quote {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  color: var(--text);
  white-space: pre-wrap;
}
</style>
