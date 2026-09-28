<script setup lang="ts">
// 给房间里的一个 AI 队友换工作电脑。挂在成员名册那一行上：换谁已经由那一行说了，
// 这里只问换到哪一台。
import type { ComputeChoice, SessionWorkLease, TopicComputeProfile } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { ApiError, setSessionWorkChoice } from '../api'
import { t } from '../i18n'
import { choiceKey, compactChoices } from '../lib/computeConfig'

const props = defineProps<{
  topicId: string
  profile: TopicComputeProfile
  session: SessionWorkLease & { choice: ComputeChoice }
  name: string
}>()
// A session's machine is part of what the room reports about itself (the
// whole-machine notice), so the room re-reads it after a change.
const emit = defineEmits<{ changed: [] }>()
const open = ref(false)
const current = ref<ComputeChoice>(props.session.choice)
const target = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
// The old machine could not be reached to push: the one case a person may
// change anyway, having been told what stays behind.
const unreachable = ref(false)
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
  return compactChoices(props.profile.project_default, standard, ...devices, current.value).map((choice) => {
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
const changed = computed(() => picked.value && choiceKey(current.value) !== choiceKey(picked.value))

async function confirm(abandonUnpushed = false) {
  const choice = picked.value
  if (!choice || !changed.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  unreachable.value = false
  try {
    const result = await setSessionWorkChoice(
      props.topicId,
      props.session.id,
      choice,
      abandonUnpushed ? { abandonUnpushed } : {}
    )
    current.value = result.session.choice ?? choice
    target.value = choiceKey(current.value)
    notice.value = t('work.sessionMachine.saved')
    emit('changed')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('global.updateFailed')
    unreachable.value = cause instanceof ApiError && cause.code === 'WorkComputerUnreachable'
  } finally {
    busy.value = false
  }
}

watch(open, (value) => {
  if (!value) return
  current.value = props.session.choice
  target.value = choiceKey(current.value)
  notice.value = ''
  error.value = ''
  unreachable.value = false
})
watch(target, () => {
  unreachable.value = false
})
</script>

<template>
  <v-dialog v-model="open" max-width="480">
    <template #activator="{ props: activator }">
      <button v-bind="activator" type="button" class="sw-action">{{ t('work.roomMachine.change') }}</button>
    </template>
    <v-card :title="t('work.sessionMachine.title', { name })">
      <v-card-text>
        <p class="mb-4">{{ t('work.sessionMachine.current', { name: current.name }) }}</p>
        <v-select
          v-model="target"
          autocomplete="off"
          :items="choices"
          :label="t('work.sessionMachine.machine')"
          :disabled="busy"
        />
        <p role="note">{{ t('work.sessionMachine.pushFirst', { name }) }}</p>
        <p v-if="notice" role="status">{{ notice }}</p>
        <p v-if="error" role="alert" class="sw-error">{{ error }}</p>
        <p v-if="unreachable" class="sw-error">{{ t('work.sessionMachine.abandonWarning') }}</p>
      </v-card-text>
      <v-card-actions>
        <v-btn variant="text" :disabled="busy" @click="open = false">{{ t('global.cancel') }}</v-btn>
        <v-spacer />
        <v-btn v-if="unreachable" variant="text" :disabled="busy" @click="confirm(true)">{{
          t('work.sessionMachine.abandon')
        }}</v-btn>
        <v-btn color="primary" variant="tonal" :disabled="busy || !changed" :loading="busy" @click="confirm()">{{
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
/* 名册里行尾那颗小动作：和「移出话题」同一档，读的是文字不是按钮块。 */
.sw-action {
  flex: none;
  margin-left: auto;
  padding: 0 2px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.sw-action:hover {
  color: var(--ink);
}
</style>
