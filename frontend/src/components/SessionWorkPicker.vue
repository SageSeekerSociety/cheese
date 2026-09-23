<script setup lang="ts">
import type { ComputeChoice, SessionWorkLease, TopicComputeProfile } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { getSessionWorkLeases, setSessionWorkChoice } from '../api'
import { t } from '../i18n'
import { choiceKey, compactChoices } from '../lib/computeConfig'

const props = defineProps<{ topicId: string; profile: TopicComputeProfile }>()
const open = ref(false)
const sessions = ref<SessionWorkLease[]>([])
const selectedId = ref('')
const target = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const selected = computed(() => sessions.value.find((session) => session.id === selectedId.value))
const sessionOptions = computed(() =>
  sessions.value.map((session) => {
    const siblings = sessions.value.filter((other) => other.agent_handle === session.agent_handle)
    return {
      title:
        siblings.length > 1
          ? t('work.sessionMachine.numberedSession', {
              name: session.agent_handle,
              number: siblings.indexOf(session) + 1,
            })
          : session.agent_handle,
      value: session.id,
    }
  })
)
const choices = computed(() => {
  const standard: ComputeChoice = {
    name: t('work.sessionMachine.cloud'),
    profile: 'cloud',
    device_id: null,
    cores: null,
    memory_mb: null,
    disk_gb: null,
  }
  const devices: ComputeChoice[] = props.profile.devices.map((device) => ({
    ...standard,
    name: device.name,
    profile: 'device',
    device_id: device.device_id,
  }))
  return compactChoices(
    props.profile.project_default,
    [...props.profile.favorites, standard, ...devices],
    selected.value?.choice
  ).map((choice) => {
    const available =
      choice.profile === 'cloud'
        ? props.profile.profiles.some((profile) => profile.id === 'cloud' && profile.available)
        : props.profile.devices.some(
            (device) => (!choice.device_id || device.device_id === choice.device_id) && device.online
          )
    return {
      title: available ? choice.name : t('work.sessionMachine.unavailable', { name: choice.name }),
      value: choiceKey(choice),
      props: { disabled: !available },
      choice,
    }
  })
})
const picked = computed(
  () => choices.value.find((choice) => choice.value === target.value && !choice.props.disabled)?.choice
)
const changed = computed(
  () => selected.value && picked.value && choiceKey(selected.value.choice) !== choiceKey(picked.value)
)

async function load() {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    sessions.value = (await getSessionWorkLeases(props.topicId)).sessions
    if (!sessions.value.some((session) => session.id === selectedId.value))
      selectedId.value = sessions.value[0]?.id ?? ''
    target.value = selected.value ? choiceKey(selected.value.choice) : ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('global.loadFailed')
  } finally {
    busy.value = false
  }
}

async function confirm() {
  const session = selected.value
  const choice = picked.value
  if (!session || !choice || !changed.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await setSessionWorkChoice(props.topicId, session.id, choice)
    sessions.value = sessions.value.map((item) => (item.id === session.id ? result.session : item))
    target.value = choiceKey(result.session.choice)
    notice.value = t('work.sessionMachine.saved')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('global.updateFailed')
  } finally {
    busy.value = false
  }
}

watch(open, (value) => {
  if (value) void load()
})
watch(selectedId, () => {
  target.value = selected.value ? choiceKey(selected.value.choice) : ''
  notice.value = ''
  error.value = ''
})
watch(
  () => props.topicId,
  () => {
    open.value = false
    sessions.value = []
    selectedId.value = ''
  }
)
</script>

<template>
  <v-dialog v-model="open" max-width="480">
    <template #activator="{ props: activator }">
      <v-btn v-bind="activator" variant="text" size="small">{{ t('work.sessionMachine.title') }}</v-btn>
    </template>
    <v-card :title="t('work.sessionMachine.title')">
      <v-card-text>
        <v-select
          v-if="sessions.length > 1"
          v-model="selectedId"
          autocomplete="off"
          :items="sessionOptions"
          :label="t('work.sessionMachine.teammate')"
          :disabled="busy"
        />
        <p v-if="!busy && !sessions.length && !error">{{ t('work.sessionMachine.empty') }}</p>
        <template v-if="selected">
          <p class="mb-4">{{ t('work.sessionMachine.current', { name: selected.choice.name }) }}</p>
          <v-select
            v-model="target"
            autocomplete="off"
            :items="choices"
            :label="t('work.sessionMachine.machine')"
            :disabled="busy"
          />
          <p role="note" class="mb-2">{{ t('work.sessionMachine.switchWarning') }}</p>
          <p>{{ t('work.sessionMachine.retained') }}</p>
        </template>
        <p v-if="notice" role="status">{{ notice }}</p>
        <p v-if="error" role="alert" class="sw-error">{{ error }}</p>
      </v-card-text>
      <v-card-actions>
        <v-btn :disabled="busy" @click="open = false">{{ t('global.close') }}</v-btn>
        <v-spacer />
        <v-btn variant="tonal" :disabled="busy || !changed" :loading="busy" @click="confirm">{{
          t('work.sessionMachine.confirm')
        }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.sw-error {
  color: var(--danger-ink);
}
</style>
