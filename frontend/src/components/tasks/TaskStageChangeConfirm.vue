<script setup lang="ts">
// 改了阶段、保存之前的那一句：谁会被判未通过（因为哪几个阶段）、谁回到进行中。名字
// 最多列 `NAMES_SHOWN` 个，再多写「等 N 人」。只画、只报：出题人选「保存更改」报
// `answer(true)`，选「取消」报 `answer(false)`。
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'

const props = defineProps<{
  /** 会跨过「未通过」那条线的人；`null` = 不用问。 */
  effect: {
    failing: number
    reopening: number
    failingNames: string[]
    reopeningNames: string[]
    failingStages: string[]
  } | null
}>()

const emit = defineEmits<{ answer: [go: boolean] }>()

const { t } = useI18n()

/** 一句话里最多点几个名字。 */
const NAMES_SHOWN = 5

function who(names: string[], count: number): string {
  const shown = names.slice(0, NAMES_SHOWN).join(t('tasks.edit.stageEffect.joiner'))
  return count > NAMES_SHOWN ? t('tasks.edit.stageEffect.whoMore', { names: shown, n: count }) : shown
}

/** 阶段在前、人在后：「「需求分析」「实现」已过截止，张三、李四还没提交，…」。 */
const failingLine = computed(() => {
  const e = props.effect
  if (!e?.failing) return ''
  const people = who(e.failingNames, e.failing)
  if (!e.failingStages.length) return t('tasks.edit.stageEffect.failingNoStage', { who: people })
  const stages = e.failingStages
    .map((name) => t('tasks.edit.stageEffect.stage', { name }))
    .join(t('tasks.edit.stageEffect.joiner'))
  return t('tasks.edit.stageEffect.failing', { stages, who: people })
})

const reopeningLine = computed(() => {
  const e = props.effect
  return e?.reopening ? t('tasks.edit.stageEffect.reopening', { who: who(e.reopeningNames, e.reopening) }) : ''
})

const open = computed({
  get: () => props.effect !== null,
  set: (value: boolean) => {
    if (!value) emit('answer', false)
  },
})
</script>

<template>
  <ConfirmDialog
    v-model="open"
    :title="t('tasks.edit.stageEffect.title')"
    :confirm-label="t('tasks.edit.saveChanges')"
    @confirm="emit('answer', true)"
  >
    <p v-if="failingLine" class="tsc__line" data-user-content>{{ failingLine }}</p>
    <p v-if="reopeningLine" class="tsc__line" data-user-content>{{ reopeningLine }}</p>
  </ConfirmDialog>
</template>

<style scoped>
.tsc__line {
  margin: 0;
}

.tsc__line + .tsc__line {
  margin-top: 8px;
}
</style>
