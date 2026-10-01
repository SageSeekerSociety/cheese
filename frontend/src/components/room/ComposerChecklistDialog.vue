<script setup lang="ts">
// 在房间里发一张自己的清单：一行一步。发出去是你的一条消息，和队友的步骤清单
// 同一种样子；之后点每一步前面的记号改它的状态，别人只能看。
//
// 规矩和队友的 `todo_write` 一样：最多 30 步，每步不超过 200 字。空行不算一步。
import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '../common/AdaptiveDialog.vue'

import { t } from '@/i18n'

const MAX_STEPS = 30
const MAX_CHARS = 200

const open = defineModel<boolean>({ default: false })

const props = defineProps<{
  /** 发出去；成功了才关上，失败时字还在框里。 */
  post: (steps: string[]) => Promise<boolean>
}>()

const text = ref('')
const saving = ref(false)

watch(open, (shown) => {
  if (shown) text.value = ''
})

const steps = computed(() =>
  text.value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
)

const problem = computed(() => {
  if (steps.value.length > MAX_STEPS) return t('work.room.checklist.tooManySteps', { max: MAX_STEPS })
  if (steps.value.some((s) => s.length > MAX_CHARS)) return t('work.room.checklist.stepTooLong', { max: MAX_CHARS })
  return ''
})

async function submit() {
  if (!steps.value.length || problem.value || saving.value) return
  saving.value = true
  try {
    if (await props.post(steps.value)) open.value = false
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.room.checklist.newTitle')"
    :primary-label="t('work.room.checklist.post')"
    primary-icon="mdi-send"
    :primary-loading="saving"
    :primary-disabled="!steps.length || !!problem"
    :close-disabled="saving"
    @primary="submit"
  >
    <v-textarea
      v-model="text"
      class="pt-2"
      variant="outlined"
      density="comfortable"
      auto-grow
      rows="4"
      autofocus
      autocomplete="off"
      :label="t('work.room.checklist.stepsLabel')"
      :hint="t('work.room.checklist.stepsHint')"
      persistent-hint
      :error-messages="problem || undefined"
    />
  </AdaptiveDialog>
</template>
