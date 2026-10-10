<template>
  <div class="text-center">
    <div v-if="countdown && !isExpired" class="countdown-display">
      <span class="countdown-number text-primary">{{ countdown.days }}</span> {{ t('shell.countdown.days') }}
      <span class="countdown-number text-primary">{{ countdown.hours }}</span> {{ t('shell.countdown.hours') }}
      <span class="countdown-number text-primary">{{ countdown.minutes }}</span> {{ t('shell.countdown.minutes') }}
      <span class="countdown-number text-primary">{{ countdown.seconds }}</span> {{ t('shell.countdown.seconds') }}
    </div>
    <div v-else class="expired-text text-error">{{ t('shell.countdown.closed') }}</div>
    <div v-if="countdown && label" class="text-caption">{{ label }}</div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import dayjs from 'dayjs'

import { deadlineState } from '@/utils/tasks'

import { t } from '@/i18n'

const props = defineProps<{
  deadline: string | Date | number
  label?: string
}>()

const countdown = ref<{ days: string; hours: string; minutes: string; seconds: string } | null>(null)
const isExpired = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const updateCountdown = () => {
  const now = dayjs()
  const deadline = dayjs(props.deadline)
  const diff = deadline.diff(now)

  // 过没过和「我的进度」读同一个 `deadlineState`，两处不会一边「今天截止」一边「已截止」。
  if (!deadlineState(deadline.valueOf(), now.valueOf())?.passed) {
    const durationObj = dayjs.duration(diff)
    countdown.value = {
      days: (durationObj.days() + durationObj.months() * 30).toString().padStart(2, '0'),
      hours: durationObj.hours().toString().padStart(2, '0'),
      minutes: durationObj.minutes().toString().padStart(2, '0'),
      seconds: durationObj.seconds().toString().padStart(2, '0'),
    }
    isExpired.value = false
  } else {
    countdown.value = null
    isExpired.value = true
  }
}

onMounted(() => {
  updateCountdown()
  timer = setInterval(updateCountdown, 1000)
})

onBeforeUnmount(() => {
  if (timer) {
    clearInterval(timer)
  }
})
</script>

<style scoped>
.countdown-display {
  font-size: 1.2rem;
}
.countdown-number {
  font-weight: bold;
  font-size: 1.4rem;
}
.expired-text {
  font-weight: bold;
  font-size: 1.2rem;
}
</style>
