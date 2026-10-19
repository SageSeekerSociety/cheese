<script setup lang="ts">
import { onMounted } from 'vue'

import { useOwnAgents } from '@/composables/useOwnAgents'
import { holdRevealGate } from '@/composables/useRevealGate'

import TeamAgentPicker from './TeamAgentPicker.vue'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{ added: [] }>()
const { state, error, saving, load, add } = useOwnAgents(() => props.projectId)
const release = holdRevealGate()
onMounted(() => load().finally(release))
async function create(connectionId: string, model: string, deviceId: string) {
  await add(connectionId, model, deviceId)
  if (!error.value) emit('added')
}
</script>
<template>
  <TeamAgentPicker :state="state" :error="error" :saving="saving" @add="create" />
</template>
