<script setup lang="ts">
import type { ProjectJoinLink } from '@/api'

import { computed, ref, watch } from 'vue'

import { getProjectJoinLink, resetProjectJoinLink, setProjectJoinApproval } from '@/api'
import { t } from '@/i18n'

const props = defineProps<{ modelValue: boolean; projectId: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const link = ref<ProjectJoinLink | null>(null)
const busy = ref(false)
const error = ref('')
const copied = ref(false)
// 重置要点两下：旧链接一重置就死，发出去的那些全都作废，不该一次误点就做掉。
const confirmingReset = ref(false)
const url = computed(() => (link.value ? `${window.location.origin}/project-invites/${link.value.token}` : ''))

watch(
  () => [props.modelValue, props.projectId] as const,
  async ([open, pid]) => {
    link.value = null
    copied.value = false
    confirmingReset.value = false
    error.value = ''
    if (!open) return
    await change(pid, () => getProjectJoinLink(pid))
  }
)

async function change(pid: string, run: () => Promise<ProjectJoinLink>) {
  busy.value = true
  error.value = ''
  try {
    const result = await run()
    if (props.projectId === pid) link.value = result
  } catch {
    error.value = t('work.joinLink.failed')
  } finally {
    busy.value = false
  }
}

async function setApproval(approval: boolean | null) {
  const pid = props.projectId
  await change(pid, () => setProjectJoinApproval(pid, !!approval))
}

async function reset() {
  if (!confirmingReset.value) {
    confirmingReset.value = true
    return
  }
  confirmingReset.value = false
  copied.value = false
  const pid = props.projectId
  await change(pid, () => resetProjectJoinLink(pid))
}

async function copy() {
  error.value = ''
  try {
    await navigator.clipboard.writeText(url.value)
    copied.value = true
  } catch {
    error.value = t('work.joinLink.copyFailed')
  }
}
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="560" @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title>{{ t('work.joinLink.title') }}</v-card-title>
      <v-card-text class="pt-4">
        <p class="t-body c-muted mb-4">{{ t('work.joinLink.description') }}</p>
        <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>
        <v-progress-linear v-if="busy" indeterminate :aria-label="t('work.joinLink.loading')" />
        <template v-if="link">
          <v-text-field
            autocomplete="off"
            :model-value="url"
            :label="t('work.joinLink.title')"
            readonly
            hide-details
            variant="outlined"
          />
          <v-switch
            :model-value="link.approval"
            :label="t('work.joinLink.approval')"
            :disabled="busy"
            color="primary"
            inset
            hide-details
            class="mt-3"
            @update:model-value="setApproval"
          />
          <p class="t-meta c-muted">
            {{ link.approval ? t('work.joinLink.approvalOn') : t('work.joinLink.approvalOff') }}
          </p>
          <p class="t-body c-muted mt-4">{{ t('work.joinLink.resetHint') }}</p>
        </template>
      </v-card-text>
      <v-card-actions class="pa-4 flex-wrap ga-2">
        <v-btn variant="text" @click="emit('update:modelValue', false)">{{ t('work.joinLink.close') }}</v-btn>
        <v-spacer />
        <v-btn
          v-if="link"
          variant="text"
          :color="confirmingReset ? 'error' : undefined"
          :disabled="busy"
          @click="reset"
        >
          {{ confirmingReset ? t('work.joinLink.resetConfirm') : t('work.joinLink.reset') }}
        </v-btn>
        <v-btn v-if="link" color="primary" variant="flat" :disabled="busy" @click="copy">
          {{ copied ? t('work.joinLink.copied') : t('work.joinLink.copy') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
