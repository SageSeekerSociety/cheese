<script setup lang="ts">
import type { ComputeChoice, TopicComputeDevice } from '../cx_types'

import { computed, ref } from 'vue'

import { t } from '../i18n'

const props = defineProps<{ devices: TopicComputeDevice[]; cloudAvailable: boolean; busy?: boolean }>()
const emit = defineEmits<{ select: [choice: ComputeChoice] }>()
const target = ref(props.cloudAvailable ? 'cloud' : props.devices[0]?.device_id ?? '')
const custom = ref(false)
const cores = ref(4)
const memory = ref(8)
const disk = ref(64)
const options = computed(() => [
  ...(props.cloudAvailable ? [{ title: t('work.computeChoice.cloud'), value: 'cloud' }] : []),
  ...props.devices.map((d) => ({
    title: t(d.online ? 'work.computeChoice.deviceOnline' : 'work.computeChoice.deviceOffline', { name: d.name }),
    value: d.device_id,
  })),
])
const valid = computed(
  () =>
    Boolean(target.value) &&
    (!custom.value || target.value !== 'cloud' || (cores.value >= 1 && memory.value >= 0.5 && disk.value >= 1))
)
function submit() {
  if (!valid.value) return
  const cloud = target.value === 'cloud'
  // Only a device is saved with a name — its own. The cloud is identified by its
  // specs and every member's screen names it in its own language (`choiceName`).
  emit('select', {
    name: cloud ? null : props.devices.find((d) => d.device_id === target.value)?.name ?? null,
    profile: cloud ? 'cloud' : 'device',
    device_id: cloud ? null : target.value,
    cores: cloud && custom.value ? Number(cores.value) : null,
    memory_mb: cloud && custom.value ? Number(memory.value) * 1024 : null,
    disk_gb: cloud && custom.value ? Number(disk.value) : null,
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
    <template v-if="target === 'cloud'">
      <v-checkbox v-model="custom" :label="t('work.computeChoice.custom')" density="compact" hide-details />
      <div v-if="custom" class="d-flex ga-2 my-2">
        <v-text-field
          v-model.number="cores"
          :label="t('work.computeChoice.cores')"
          type="number"
          min="1"
          density="compact"
          variant="outlined"
          hide-details
        />
        <v-text-field
          v-model.number="memory"
          :label="t('work.computeChoice.memory')"
          type="number"
          min="0.5"
          step="0.5"
          density="compact"
          variant="outlined"
          hide-details
        />
        <v-text-field
          v-model.number="disk"
          :label="t('work.computeChoice.disk')"
          type="number"
          min="1"
          density="compact"
          variant="outlined"
          hide-details
        />
      </div>
      <p class="text-body-2 text-medium-emphasis my-3">{{ t('work.computeChoice.cloudHint') }}</p>
    </template>
    <p v-else-if="target" class="text-body-2 text-medium-emphasis my-3">
      {{ t('work.computeChoice.deviceHint') }}
    </p>
    <v-btn class="mt-3" color="primary" variant="tonal" :disabled="!valid || busy" :loading="busy" @click="submit">{{
      t('work.computeChoice.submit')
    }}</v-btn>
  </div>
</template>
