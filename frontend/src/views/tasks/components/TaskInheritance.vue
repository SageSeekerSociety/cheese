<script setup lang="ts">
// 「领这道题 / 从这道题建项目，会继承到什么」 (#944)。只吃 props —— 取数在
// `composables/useTaskInheritance.ts`，三处入口共用这一块渲染。
//
// 三样东西，缺一不可：资源包（吃到多少算力）、合成后的「给 AI 队友的指导」
// （**连它来自哪一层一起说**，空间 / 项目集 / 题目 / 项目）、以及会被带上的资料。
// 层名不是装饰：一份指导说「来自项目集」和「来自这道题」对人是两件事 —— 前者改
// 一次二十道题都变，后者只有这一道。
//
// 合成在服务端由 `protocol.resolve()` 那一个循环算出来（`teaching.source` 也是
// 它带出来的），这一块只把它摆出来，不重算、不猜。
import type { TaskInheritanceData, TaskInheritanceSource } from '@/network/api/tasks/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  inheritance: TaskInheritanceData | null
  loading?: boolean
}>()

const { t } = useI18n()

// 层名 → 文案键。一张表而不是拼字符串：拼出来的键在换语言或改键名时会静默落空。
const SOURCE_KEYS: Record<TaskInheritanceSource, string> = {
  space: 'tasks.inheritance.from.space',
  category: 'tasks.inheritance.from.category',
  task: 'tasks.inheritance.from.task',
  project: 'tasks.inheritance.from.project',
}

const teaching = computed(() => props.inheritance?.teaching ?? null)

const credits = computed(() => {
  const value = props.inheritance?.resourcePack?.compute_credits
  return typeof value === 'number' && value > 0 ? value : null
})

const sourceLabel = computed(() => {
  const source = teaching.value?.source
  return source ? t(SOURCE_KEYS[source]) : null
})

/** 指导里有没有一句话可说 —— 四层都没说时连标题都不摆。 */
const hasTeaching = computed(() => {
  const g = teaching.value
  if (!g) return false
  return (
    !!g.systemPrompt ||
    (g.currentWeek !== null && g.currentWeek !== undefined) ||
    !!g.allowedTopics?.length ||
    !!g.avoidInCode?.length
  )
})

const materials = computed(() => props.inheritance?.materials ?? [])
</script>

<template>
  <section v-if="loading || inheritance" class="inheritance">
    <div class="inheritance__title">{{ t('tasks.inheritance.title') }}</div>

    <v-progress-linear v-if="loading && !inheritance" indeterminate color="primary" height="2" />

    <template v-if="inheritance">
      <!-- 资源包：算力。没有额度时也要说一句，不能整行消失（「没写」和「没有」是
           两件事，留白会被读成后者）。 -->
      <div class="inheritance__row">
        <span class="inheritance__label">{{ t('tasks.inheritance.pack') }}</span>
        <span v-if="credits !== null" class="inheritance__value t-num">
          {{ t('tasks.inheritance.credits', { n: credits }) }}
        </span>
        <span v-else class="inheritance__value inheritance__value--none">
          {{ t('tasks.inheritance.packNone') }}
        </span>
      </div>

      <!-- 指导：来源层写在标题旁边，是这块的重点。 -->
      <div class="inheritance__row">
        <span class="inheritance__label">{{ t('tasks.inheritance.guidance') }}</span>
        <span v-if="sourceLabel" class="inheritance__value">{{ sourceLabel }}</span>
        <span v-else class="inheritance__value inheritance__value--none">
          {{ t('tasks.inheritance.guidanceNone') }}
        </span>
      </div>

      <div v-if="hasTeaching" class="inheritance__body">
        <p v-if="teaching?.systemPrompt" class="inheritance__prompt">{{ teaching.systemPrompt }}</p>
        <p v-if="teaching?.currentWeek !== null && teaching?.currentWeek !== undefined">
          {{ t('tasks.inheritance.week', { n: teaching.currentWeek }) }}
        </p>
        <div v-if="teaching?.allowedTopics?.length" class="inheritance__list">
          <span class="inheritance__sublabel">{{ t('tasks.inheritance.topics') }}</span>
          <ul>
            <li v-for="topic in teaching.allowedTopics" :key="topic">{{ topic }}</li>
          </ul>
        </div>
        <div v-if="teaching?.avoidInCode?.length" class="inheritance__list">
          <span class="inheritance__sublabel">{{ t('tasks.inheritance.avoid') }}</span>
          <ul>
            <li v-for="item in teaching.avoidInCode" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>

      <!-- 会被带上的资料：板子资料库里「所有成员」那一档。 -->
      <div class="inheritance__row">
        <span class="inheritance__label">{{ t('tasks.inheritance.materials') }}</span>
        <span class="inheritance__value t-num">{{
          t('tasks.inheritance.materialCount', { n: materials.length })
        }}</span>
      </div>
      <ul v-if="materials.length" class="inheritance__materials">
        <li v-for="material in materials" :key="material.id">{{ material.name }}</li>
      </ul>
      <p v-else class="inheritance__value inheritance__value--none">
        {{ t('tasks.inheritance.materialsNone') }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.inheritance {
  padding: 12px 0;
  border-top: 1px solid var(--line);
}

.inheritance__title {
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 8px;
}

.inheritance__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 3px 0;
}

.inheritance__label {
  color: var(--muted);
  min-width: 5em;
}

.inheritance__value {
  color: var(--ink);
}

.inheritance__value--none {
  color: var(--faint);
}

.inheritance__body {
  padding: 4px 0 6px 5em;
  color: var(--text);
}

.inheritance__prompt {
  white-space: pre-wrap;
  margin-bottom: 4px;
}

.inheritance__sublabel {
  color: var(--muted);
}

.inheritance__list ul,
.inheritance__materials {
  margin: 0;
  padding-left: 1.2em;
}

.inheritance__materials {
  padding-left: calc(5em + 1.2em);
}
</style>
