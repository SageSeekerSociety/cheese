<script setup lang="ts">
import type { ComputeChoice, TopicComputeDevice } from '../cx_types'

import { computed, ref } from 'vue'

import { t } from '../i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const props = defineProps<{
  devices: TopicComputeDevice[]
  cloudAvailable: boolean
  busy?: boolean
}>()
const emit = defineEmits<{ select: [choice: ComputeChoice] }>()
const target = ref(props.cloudAvailable ? 'cloud' : props.devices[0]?.device_id ?? '')
const options = computed(() => [
  ...(props.cloudAvailable ? [{ title: t('work.computeChoice.cloud'), value: 'cloud' }] : []),
  ...props.devices.map((d) => ({
    title: t(d.online ? 'work.computeChoice.deviceOnline' : 'work.computeChoice.deviceOffline', { name: d.name }),
    value: d.device_id,
  })),
])

function submit() {
  if (!target.value) return
  const cloud = target.value === 'cloud'
  // Only a device is saved with a name — its own. The cloud sandbox has none, and
  // every member's screen names it in its own language (`choiceName`).
  emit('select', {
    name: cloud ? null : props.devices.find((d) => d.device_id === target.value)?.name ?? null,
    profile: cloud ? 'cloud' : 'device',
    device_id: cloud ? null : target.value,
  })
}
</script>

<template>
  <div class="pa-3">
    <v-select
      v-model="target"
      autocomplete="off"
      :items="options"
      :label="t('work.computeChoice.computer')"
      density="compact"
      variant="outlined"
      hide-details
    />
    <p v-if="!options.length" class="text-body-2 my-3">{{ t('work.computeChoice.none') }}</p>
    <p v-if="target === 'cloud'" class="text-body-2 text-medium-emphasis my-3">
      {{ t('work.computeChoice.cloudHint') }}
    </p>
    <p v-else-if="target" class="text-body-2 text-medium-emphasis my-3">
      {{ t('work.computeChoice.deviceHint') }}
    </p>
    <BaseButton kind="primary" class="mt-3" :disabled="!target || busy" :loading="busy" @click="submit">{{
      t('work.computeChoice.submit')
    }}</BaseButton>
  </div>
</template>
