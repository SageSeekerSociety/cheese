<script setup lang="ts">
import type { ProjectJoinLink } from '@/api'

import { computed, ref, watch } from 'vue'

import { createProjectJoinLink, getProjectJoinLink, revokeProjectJoinLink } from '@/api'
import { t } from '@/i18n'

const props = defineProps<{ modelValue: boolean; projectId: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const link = ref<ProjectJoinLink | null>(null)
const busy = ref(false)
const error = ref('')
const copied = ref(false)
const url = computed(() => (link.value ? `${window.location.origin}/project-invites/${link.value.token}` : ''))

watch(
  () => [props.modelValue, props.projectId] as const,
  async ([open, pid]) => {
    link.value = null
    copied.value = false
    error.value = ''
    if (!open) return
    busy.value = true
    try {
      const result = await getProjectJoinLink(pid)
      if (props.projectId === pid) link.value = result
    } catch {
      error.value = t('work.joinLink.failed')
    } finally {
      busy.value = false
    }
  }
)

async function changeLink(revoke: boolean) {
  busy.value = true
  error.value = ''
  copied.value = false
  const pid = props.projectId
  try {
    if (revoke) {
      await revokeProjectJoinLink(pid)
      if (props.projectId === pid) link.value = null
    } else {
      const result = await createProjectJoinLink(pid)
      if (props.projectId === pid) link.value = result
    }
  } catch {
    error.value = t('work.joinLink.failed')
  } finally {
    busy.value = false
  }
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
          <p class="t-meta c-muted mt-3">
            {{ t('work.joinLink.expires', { date: new Date(link.expires_at).toLocaleString() }) }}
          </p>
          <p class="t-body c-muted mt-3">{{ t('work.joinLink.revokeHint') }}</p>
        </template>
        <p v-else-if="!busy" class="t-body c-muted">{{ t('work.joinLink.empty') }}</p>
      </v-card-text>
      <v-card-actions class="pa-4 flex-wrap ga-2">
        <v-btn variant="text" @click="emit('update:modelValue', false)">{{ t('work.joinLink.close') }}</v-btn>
        <v-spacer />
        <v-btn v-if="link" variant="text" :disabled="busy" @click="changeLink(true)">{{
          t('work.joinLink.revoke')
        }}</v-btn>
        <v-btn v-if="link" color="primary" variant="flat" :disabled="busy" @click="copy">
          {{ copied ? t('work.joinLink.copied') : t('work.joinLink.copy') }}
        </v-btn>
        <v-btn v-else color="primary" variant="flat" :loading="busy" @click="changeLink(false)">{{
          t('work.joinLink.create')
        }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
