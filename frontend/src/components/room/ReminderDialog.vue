<script setup lang="ts">
// 「提醒我」：在这个房间里给自己定一个时刻，到点收到一条通知，点开回到这里。
//
// 提醒只给自己。它不进房间的对话，房间里的其他人看不见（后端
// `delivery/timer.py` 的 `_hand_to_person`）。
import { computed, ref, watch } from 'vue'

import { useReminder } from '@/composables/useReminder'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{ topicId: string }>()
const open = defineModel<boolean>({ required: true })

const emit = defineEmits<{ (e: 'set', at: Date): void }>()

/** `<input type="datetime-local">` 的值：本地时间，到分钟。 */
function localValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 默认一小时之后，取整到五分钟：最常见的「待会儿提醒我」。 */
function defaultTime(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000)
  d.setMinutes(Math.ceil(d.getMinutes() / 5) * 5, 0, 0)
  return localValue(d)
}

const when = ref(defaultTime())
const content = ref('')
const { saving, error, set } = useReminder()

watch(open, (v) => {
  if (!v) return
  when.value = defaultTime()
  content.value = ''
  error.value = null
})

// 本地时间串交给 Date 按本地时区读，发出去时换成带时区的 ISO。
const at = computed(() => {
  const d = new Date(when.value)
  return Number.isNaN(d.getTime()) ? null : d
})
const inPast = computed(() => at.value !== null && at.value.getTime() <= Date.now())
const valid = computed(() => at.value !== null && !inPast.value && content.value.trim() !== '')

async function submit() {
  if (!valid.value || !at.value) return
  const chosen = at.value
  if (!(await set(props.topicId, chosen, content.value.trim()))) return
  open.value = false
  emit('set', chosen)
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.room.reminder.title')"
    :primary-label="t('work.room.reminder.submit')"
    :primary-loading="saving"
    :primary-disabled="!valid"
    :close-disabled="saving"
    :max-width="420"
    @primary="submit"
  >
    <v-text-field
      v-model="when"
      type="datetime-local"
      autocomplete="off"
      density="compact"
      variant="outlined"
      :min="localValue(new Date())"
      :label="t('work.room.reminder.when')"
      :error-messages="inPast ? t('work.room.reminder.inPast') : undefined"
      data-testid="reminder-when"
    />
    <v-textarea
      v-model="content"
      autocomplete="off"
      density="compact"
      variant="outlined"
      rows="2"
      auto-grow
      max-rows="6"
      :label="t('work.room.reminder.content')"
      data-testid="reminder-content"
    />
    <v-alert v-if="error" type="error" density="comfortable" class="mt-2">
      {{ error }}
    </v-alert>
  </AdaptiveDialog>
</template>
