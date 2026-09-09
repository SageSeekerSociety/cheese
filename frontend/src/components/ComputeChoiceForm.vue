<script setup lang="ts">
import type { ComputeChoice, TopicComputeDevice } from '../cx_types'

import { computed, ref } from 'vue'

const props = defineProps<{ devices: TopicComputeDevice[]; cloudAvailable: boolean; busy?: boolean; named?: boolean }>()
const emit = defineEmits<{ select: [choice: ComputeChoice] }>()
const target = ref(props.cloudAvailable ? 'cloud' : props.devices[0]?.device_id ?? '')
const name = ref('')
const custom = ref(false)
const cores = ref(4)
const memory = ref(8)
const disk = ref(64)
const options = computed(() => [
  ...(props.cloudAvailable ? [{ title: '云端', value: 'cloud' }] : []),
  ...props.devices.map((d) => ({ title: `${d.name} · ${d.online ? '在线' : '离线'}`, value: d.device_id })),
])
const valid = computed(
  () =>
    Boolean(target.value) &&
    (!props.named || name.value.trim()) &&
    (!custom.value || target.value !== 'cloud' || (cores.value >= 1 && memory.value >= 0.5 && disk.value >= 1))
)
function submit() {
  if (!valid.value) return
  const cloud = target.value === 'cloud'
  emit('select', {
    name:
      name.value.trim() ||
      (cloud
        ? custom.value
          ? '云端 · 自定义配置'
          : '云端 · 标准配置'
        : props.devices.find((d) => d.device_id === target.value)?.name ?? '自有设备'),
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
    <v-select v-model="target" :items="options" label="运行资源" density="compact" variant="outlined" hide-details />
    <p v-if="!options.length" class="text-body-2 my-3">暂无可用资源，请在团队算力页添加设备或接入云服务</p>
    <template v-if="target === 'cloud'">
      <v-checkbox v-model="custom" label="自定义 CPU、内存和磁盘" density="compact" hide-details />
      <div v-if="custom" class="d-flex ga-2 my-2">
        <v-text-field
          v-model.number="cores"
          label="CPU 核"
          type="number"
          min="1"
          density="compact"
          variant="outlined"
          hide-details
        />
        <v-text-field
          v-model.number="memory"
          label="内存 GB"
          type="number"
          min="0.5"
          step="0.5"
          density="compact"
          variant="outlined"
          hide-details
        />
        <v-text-field
          v-model.number="disk"
          label="磁盘 GB"
          type="number"
          min="1"
          density="compact"
          variant="outlined"
          hide-details
        />
      </div>
      <p class="text-body-2 text-medium-emphasis my-3">首次运行时分配，计入团队云额度；超出供应范围时会提示调整</p>
    </template>
    <p v-else-if="target" class="text-body-2 text-medium-emphasis my-3">
      设备已加入团队，无需再次授权；离线时需要等待设备上线
    </p>
    <v-text-field
      v-if="named"
      v-model="name"
      autocomplete="off"
      label="常用配置名称"
      maxlength="60"
      density="compact"
      variant="outlined"
      class="mt-3"
      hide-details
    />
    <v-btn class="mt-3" variant="tonal" :disabled="!valid || busy" :loading="busy" @click="submit">{{
      named ? '保存到项目常用' : '用于当前房间'
    }}</v-btn>
  </div>
</template>
