<script setup lang="ts">
import type { CloudSupply, SupplyBound } from '../composables/useCloudSupply'
import type { ComputeChoice, TopicComputeDevice } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { t } from '../i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const props = defineProps<{
  devices: TopicComputeDevice[]
  cloudAvailable: boolean
  // 云端此刻能开的范围：由挂它的地方去问（`useCloudSupply`），这里只照着画。
  // null 是还没问；问不到的那一种带着原因，不拿平台自己的上下限冒充。
  supply: CloudSupply | null
  supplyLoading?: boolean
  busy?: boolean
}>()
const emit = defineEmits<{ select: [choice: ComputeChoice]; needSupply: [] }>()
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

// 可选范围只在要自定义的时候才要。
watch(
  () => target.value === 'cloud' && custom.value,
  (wanted) => {
    if (wanted && !props.supply) emit('needSupply')
  },
  { immediate: true }
)
const supplyLoading = computed(() => Boolean(props.supplyLoading))
const ranges = computed(() => (props.supply?.available ? props.supply.selectable : null))

function gb(mb: number): string {
  return `${Number((mb / 1024).toFixed(3))}`
}
function outside(value: number, bound: SupplyBound | null | undefined, scale = 1): string {
  if (!ranges.value) return ''
  if (!bound) return t('work.computeChoice.noValue')
  const v = value * scale
  return v >= bound.min && v <= bound.max ? '' : t('work.computeChoice.outOfRange')
}
const coresError = computed(() => outside(Number(cores.value), ranges.value?.cores))
const memoryError = computed(() => outside(Number(memory.value), ranges.value?.memory_mb, 1024))
const diskError = computed(() => outside(Number(disk.value), ranges.value?.disk_gb))
const rangeText = computed(() => {
  const r = ranges.value
  if (!r) return ''
  const part = (label: string, b: SupplyBound | null, fmt: (n: number) => string, unit: string) =>
    b ? `${label} ${fmt(b.min)}–${fmt(b.max)} ${unit}` : t('work.computeChoice.noRangeFor', { label })
  return [
    part('CPU', r.cores, String, t('work.computeChoice.coresUnit')),
    part(t('work.computeChoice.memoryLabel'), r.memory_mb, gb, 'GB'),
    part(t('work.computeChoice.diskLabel'), r.disk_gb, String, 'GB'),
  ].join(' · ')
})

const valid = computed(
  () =>
    Boolean(target.value) &&
    (!custom.value ||
      target.value !== 'cloud' ||
      (cores.value >= 1 &&
        memory.value >= 0.5 &&
        disk.value >= 1 &&
        !supplyLoading.value &&
        !coresError.value &&
        !memoryError.value &&
        !diskError.value))
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
    memory_mb: cloud && custom.value ? Math.round(Number(memory.value) * 1024) : null,
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
      <template v-if="custom">
        <p v-if="supplyLoading" class="text-body-2 text-medium-emphasis my-2" data-testid="supply-loading">
          {{ t('work.computeChoice.supplyLoading') }}
        </p>
        <p v-else-if="ranges" class="text-body-2 my-2" data-testid="supply-range">
          {{ t('work.computeChoice.supplyRange', { range: rangeText }) }}
        </p>
        <p
          v-else-if="props.supply && !props.supply.available"
          class="text-body-2 text-warning my-2"
          data-testid="supply-unknown"
        >
          {{ t('work.computeChoice.supplyUnknown', { reason: props.supply.reason }) }}
        </p>
        <div class="d-flex ga-2 my-2">
          <v-text-field
            v-model.number="cores"
            :label="t('work.computeChoice.cores')"
            type="number"
            :min="ranges?.cores?.min ?? 1"
            :max="ranges?.cores?.max"
            density="compact"
            variant="outlined"
            :error-messages="coresError"
            :hide-details="!coresError"
          />
          <v-text-field
            v-model.number="memory"
            :label="t('work.computeChoice.memory')"
            type="number"
            :min="ranges?.memory_mb ? ranges.memory_mb.min / 1024 : 0.5"
            :max="ranges?.memory_mb ? ranges.memory_mb.max / 1024 : undefined"
            step="0.5"
            density="compact"
            variant="outlined"
            :error-messages="memoryError"
            :hide-details="!memoryError"
          />
          <v-text-field
            v-model.number="disk"
            :label="t('work.computeChoice.disk')"
            type="number"
            :min="ranges?.disk_gb?.min ?? 1"
            :max="ranges?.disk_gb?.max"
            density="compact"
            variant="outlined"
            :error-messages="diskError"
            :hide-details="!diskError"
          />
        </div>
      </template>
      <p class="text-body-2 text-medium-emphasis my-3">{{ t('work.computeChoice.cloudHint') }}</p>
    </template>
    <p v-else-if="target" class="text-body-2 text-medium-emphasis my-3">
      {{ t('work.computeChoice.deviceHint') }}
    </p>
    <BaseButton kind="primary" class="mt-3" :disabled="!valid || busy" :loading="busy" @click="submit">{{
      t('work.computeChoice.submit')
    }}</BaseButton>
  </div>
</template>
