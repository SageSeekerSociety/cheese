<script setup lang="ts">
// 改了阶段、保存之前的那一句：会有几个人被判未通过、几个人回到进行中。只画、只报：
// 出题人选「保存更改」报 `answer(true)`，选「取消」报 `answer(false)`。
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'

const props = defineProps<{
  /** 会跨过「未通过」那条线的人数；`null` = 不用问。 */
  effect: { failing: number; reopening: number } | null
}>()

const emit = defineEmits<{ answer: [go: boolean] }>()

const { t } = useI18n()

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
    <p v-if="effect?.failing" class="tsc__line">{{ t('tasks.edit.stageEffect.failing', { n: effect.failing }) }}</p>
    <p v-if="effect?.reopening" class="tsc__line">
      {{ t('tasks.edit.stageEffect.reopening', { n: effect.reopening }) }}
    </p>
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
