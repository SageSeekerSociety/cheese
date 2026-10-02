<script setup lang="ts">
import { onMounted } from 'vue'

import { useCreditUsage } from '@/composables/useCreditUsage'

import UsageView from './UsageView.vue'

import { getMyCreditUsage } from '@/api/creditUsage'

// 个人设置里的「芝士额度」（`/users/settings/usage`）。取数在这里，画法在 `UsageView`。
defineOptions({ name: 'UsageSettings' })

const { usage, loading, error, load } = useCreditUsage(getMyCreditUsage)

onMounted(load)
</script>

<template>
  <UsageView :usage="usage" :loading="loading" :error="error" @retry="load" />
</template>
