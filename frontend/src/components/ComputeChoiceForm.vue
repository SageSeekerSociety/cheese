<script setup lang="ts">
import type { ComputeChoice, TopicComputeDevice } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { type SupplyBound, useCloudSupply } from '../composables/useCloudSupply'

const props = defineProps<{
  devices: TopicComputeDevice[]
  cloudAvailable: boolean
  projectId: string
  busy?: boolean
}>()
const emit = defineEmits<{ select: [choice: ComputeChoice] }>()
const target = ref(props.cloudAvailable ? 'cloud' : props.devices[0]?.device_id ?? '')
const custom = ref(false)
const cores = ref(4)
const memory = ref(8)
const disk = ref(64)
const options = computed(() => [
  ...(props.cloudAvailable ? [{ title: '云端', value: 'cloud' }] : []),
  ...props.devices.map((d) => ({ title: `${d.name} · ${d.online ? '在线' : '离线'}`, value: d.device_id })),
])

// 可选范围只在要自定义的时候去问：它来自云端此刻的供应，问不到就照实说问不到，
// 不拿平台自己的上下限冒充。
const { supply, loading: supplyLoading, load: loadSupply } = useCloudSupply(() => props.projectId)
watch(
  () => target.value === 'cloud' && custom.value,
  (wanted) => {
    if (wanted) void loadSupply()
  },
  { immediate: true }
)
const ranges = computed(() => (supply.value?.available ? supply.value.selectable : null))

function gb(mb: number): string {
  return `${Number((mb / 1024).toFixed(3))}`
}
function outside(value: number, bound: SupplyBound | null | undefined, scale = 1): string {
  if (!ranges.value) return ''
  if (!bound) return '当前没有可选值'
  const v = value * scale
  return v >= bound.min && v <= bound.max ? '' : '超出可选范围'
}
const coresError = computed(() => outside(Number(cores.value), ranges.value?.cores))
const memoryError = computed(() => outside(Number(memory.value), ranges.value?.memory_mb, 1024))
const diskError = computed(() => outside(Number(disk.value), ranges.value?.disk_gb))
const rangeText = computed(() => {
  const r = ranges.value
  if (!r) return ''
  const part = (label: string, b: SupplyBound | null, fmt: (n: number) => string, unit: string) =>
    b ? `${label} ${fmt(b.min)}–${fmt(b.max)} ${unit}` : `${label}暂无可选值`
  return [
    part('CPU', r.cores, String, '核'),
    part('内存', r.memory_mb, gb, 'GB'),
    part('磁盘', r.disk_gb, String, 'GB'),
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
  emit('select', {
    name: cloud
      ? custom.value
        ? '云端 · 自定义配置'
        : '云端 · 标准配置'
      : props.devices.find((d) => d.device_id === target.value)?.name ?? '自有设备',
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
      label="工作电脑"
      density="compact"
      variant="outlined"
      hide-details
    />
    <p v-if="!options.length" class="text-body-2 my-3">暂无可用的工作电脑</p>
    <template v-if="target === 'cloud'">
      <v-checkbox v-model="custom" label="自定义 CPU、内存和磁盘" density="compact" hide-details />
      <template v-if="custom">
        <p v-if="supplyLoading" class="text-body-2 text-medium-emphasis my-2" data-testid="supply-loading">
          正在查询云端当前可选范围…
        </p>
        <p v-else-if="ranges" class="text-body-2 my-2" data-testid="supply-range">可选范围：{{ rangeText }}</p>
        <p v-else-if="supply && !supply.available" class="text-body-2 text-warning my-2" data-testid="supply-unknown">
          暂时查不到云端可选范围（{{ supply.reason }}）。可以先保存，开机时由云端校验。
        </p>
        <div class="d-flex ga-2 my-2">
          <v-text-field
            v-model.number="cores"
            label="CPU 核"
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
            label="内存 GB"
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
            label="磁盘 GB"
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
      <p class="text-body-2 text-medium-emphasis my-3">
        首次运行时分配，计入团队云额度。云端不报告剩余容量，范围内的配置开机时仍可能创建失败。
      </p>
    </template>
    <p v-else-if="target" class="text-body-2 text-medium-emphasis my-3">
      设备已加入团队，无需再次授权；离线时需要等待设备上线
    </p>
    <v-btn class="mt-3" color="primary" variant="tonal" :disabled="!valid || busy" :loading="busy" @click="submit"
      >使用此配置</v-btn
    >
  </div>
</template>
