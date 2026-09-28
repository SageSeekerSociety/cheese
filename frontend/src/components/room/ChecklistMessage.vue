<script setup lang="ts">
// 队友的步骤清单（`todo_write`）在对话里的样子。它就是队友发的一条消息，每次更新
// 都是改这同一条：一步一行，前面一个记号——✱ 加粗是正在做的，○ 是还没做的，✓ 淡下
// 去是做完的；做完时下面接一句结果；最底下一行小字说清单什么时候更新的。
//
// 记号用图标，不用字符：✱ ○ ✓ 的字重和基线随系统字体变，13px 上对不齐。消息正文里
// 写的是这几个字符，给看不到这里的读者（翻记录的队友、复制、通知预览）。
import type { ChecklistMeta, TodoItem } from '../../cx_types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { relTime } from '../../lib/relTime'

import { t } from '@/i18n'

const props = defineProps<{
  checklist: ChecklistMeta
  /** 清单最后一次写下的时刻：改过就是改的时刻，没改过就是发出的时刻。 */
  updatedAt: string
  edited: boolean
}>()

const MARK: Record<TodoItem['status'], string> = {
  in_progress: 'mdi-asterisk',
  pending: 'mdi-circle-outline',
  completed: 'mdi-check',
}

// 一小时以内说「刚刚 / N 分钟前」，再往前说钟点：一直开着的房间里这句话得跟着变，
// 所以一小时以内每半分钟重算一次，过了一小时就不再动。
const RELATIVE_FOR_MS = 60 * 60 * 1000
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
function stopTicking() {
  clearInterval(timer)
  timer = undefined
}
watch(
  () => props.updatedAt,
  () => {
    now.value = Date.now()
    stopTicking()
    if (now.value - Date.parse(props.updatedAt) < RELATIVE_FOR_MS) {
      timer = setInterval(() => {
        now.value = Date.now()
        if (now.value - Date.parse(props.updatedAt) >= RELATIVE_FOR_MS) stopTicking()
      }, 30_000)
    }
  },
  { immediate: true }
)
onBeforeUnmount(stopTicking)

const when = computed(() => {
  const at = new Date(props.updatedAt)
  const age = now.value - at.getTime()
  if (age < RELATIVE_FOR_MS) return relTime(props.updatedAt)
  const clock = at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (new Date(now.value).toDateString() === at.toDateString()) return clock
  const day = `${String(at.getMonth() + 1).padStart(2, '0')}-${String(at.getDate()).padStart(2, '0')}`
  return `${day} ${clock}`
})
</script>

<template>
  <div class="checklist">
    <ul class="checklist__items">
      <li v-for="item in checklist.items" :key="item.id" class="checklist__item" :class="`is-${item.status}`">
        <v-icon class="checklist__mark" size="14">{{ MARK[item.status] }}</v-icon>
        <span>{{ item.subject }}</span>
      </li>
    </ul>
    <div v-if="checklist.result" class="checklist__result">
      <v-icon class="checklist__result-mark c-ok" size="15">mdi-check-circle</v-icon>
      <span>{{ checklist.result }}</span>
    </div>
    <div class="checklist__footer">
      <span>{{ t('work.room.checklist.updatedAt', { time: when }) }}</span>
      <span v-if="edited" class="checklist__edited">{{ t('work.room.message.edited') }}</span>
    </div>
  </div>
</template>

<style scoped>
.checklist__items {
  margin: 0;
  padding: 0;
  list-style: none;
}
.checklist__item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 1px 0;
  font-size: 14px;
  line-height: var(--lh-14-loose);
  color: var(--text);
}
/* 图标盒子没有文字基线：整行顶对齐，再把图标压到第一行文字的中线上
   ((22px − 14px) / 2 = 4px)。 */
.checklist__mark {
  flex: none;
  margin-top: 4px;
  color: var(--faint);
}
/* 正在做的那一步：记号和字都加深，字加粗。不上琥珀——它是进度，不是要人去按的东西。 */
.checklist__item.is-in_progress {
  color: var(--ink);
  font-weight: 600;
}
.checklist__item.is-in_progress .checklist__mark {
  color: var(--ink);
}
.checklist__item.is-pending .checklist__mark {
  color: var(--muted);
}
/* 做完的淡下去，不划线：一整列划掉的字比淡下去的字更难扫。 */
.checklist__item.is-completed {
  color: var(--faint);
}
.checklist__result {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 6px;
  font-size: 14px;
  line-height: var(--lh-14-loose);
  color: var(--ink);
}
.checklist__result-mark {
  flex: none;
  margin-top: 3px;
}
.checklist__footer {
  display: flex;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}
</style>
