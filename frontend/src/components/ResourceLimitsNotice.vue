<script setup lang="ts">
import type { ResourceLimits } from '@/api'

import { onMounted, ref } from 'vue'

import { getResourceLimits } from '@/api'

const limits = ref<ResourceLimits | null>(null)
const failed = ref(false)

async function load() {
  failed.value = false
  try {
    limits.value = await getResourceLimits()
  } catch {
    failed.value = true
  }
}

onMounted(load)
</script>

<template>
  <div class="text-body-2 text-medium-emphasis my-3" role="status">
    <template v-if="limits">
      <div>项目数量：当前未设置上限</div>
      <div>运行环境按需创建；新项目默认最多同时运行 {{ limits.max_concurrent_turns }} 个 AI 任务，超出后排队</div>
      <div>云虚拟机：团队默认共享 {{ limits.max_machines_per_team }} 台名额，实际额度与用量可在团队算力页查看</div>
    </template>
    <template v-else-if="failed">
      资源限制加载失败
      <v-btn size="small" variant="text" @click="load">重试</v-btn>
    </template>
    <template v-else>正在加载资源限制…</template>
  </div>
</template>
