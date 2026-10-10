<script setup lang="ts">
import type { ComputeChoice, TopicComputeDevice } from '../types/compute'

import { computed, ref } from 'vue'

import { t } from '../i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const props = defineProps<{
  devices: TopicComputeDevice[]
  cloudAvailable: boolean
  cloudVmAvailable?: boolean
  busy?: boolean
}>()
const emit = defineEmits<{ select: [choice: ComputeChoice] }>()
// The whole-VM option's value; a device id is never this.
const CLOUD_VM = 'cloud:whole-machine'
const target = ref(props.cloudAvailable ? 'cloud' : props.devices[0]?.device_id ?? '')
const options = computed(() => [
  ...(props.cloudAvailable ? [{ title: t('work.computeChoice.cloud'), value: 'cloud' }] : []),
  ...(props.cloudAvailable && props.cloudVmAvailable
    ? [{ title: t('work.computeChoice.cloudVm'), value: CLOUD_VM }]
    : []),
  ...props.devices.map((d) => ({
    title: t(d.online ? 'work.computeChoice.deviceOnline' : 'work.computeChoice.deviceOffline', { name: d.name }),
    value: d.device_id,
  })),
])

function submit() {
  if (!target.value) return
  const wholeMachine = target.value === CLOUD_VM
  const cloud = target.value === 'cloud' || wholeMachine
  // Only a device is saved with a name — its own. The cloud sandbox and the cloud
  // VM have none, and every member's screen names them in its own language
  // (`choiceName`).
  emit('select', {
    name: cloud ? null : props.devices.find((d) => d.device_id === target.value)?.name ?? null,
    profile: cloud ? 'cloud' : 'device',
    device_id: cloud ? null : target.value,
    ...(cloud ? { whole_machine: wholeMachine } : {}),
  })
}
</script>

<template>
  <div class="pa-3">
    <v-select
      v-model="target"
      autocomplete="off"
      :items="options"
      :label="t('work.computeChoice.environment')"
      density="compact"
      variant="outlined"
      hide-details
    />
    <p v-if="!options.length" class="text-body-2 my-3">{{ t('work.computeChoice.none') }}</p>
    <p v-if="target === 'cloud'" class="text-body-2 text-medium-emphasis my-3">
      {{ t('work.computeChoice.cloudHint') }}
    </p>
    <p v-else-if="target === CLOUD_VM" class="text-body-2 text-medium-emphasis my-3">
      {{ t('work.computeChoice.cloudVmHint') }}
    </p>
    <p v-else-if="target" class="text-body-2 text-medium-emphasis my-3">
      {{ t('work.computeChoice.deviceHint') }}
    </p>
    <BaseButton kind="primary" class="mt-3" :disabled="!target || busy" :loading="busy" @click="submit">{{
      t('work.computeChoice.submit')
    }}</BaseButton>
  </div>
</template>
