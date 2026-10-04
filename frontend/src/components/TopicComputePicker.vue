<script setup lang="ts">
// 房间这一项：这个话题在哪台工作电脑上跑。一个话题一个容器（2026-09-28，推翻结论
// 60）：改它就是整个房间一起搬，房间里的每个 AI 队友都换过去。挂在成员名册里房间
// 那一行上。
import type { ComputeChoice, TopicComputeProfile } from '../cx_types'

import { computed, ref } from 'vue'

import { ApiError, setTopicComputeChoice } from '../api'
import { t } from '../i18n'
import { choiceDetail, choiceKey, choiceName, compactChoices } from '../lib/computeConfig'

import ComputeChoiceForm from './ComputeChoiceForm.vue'

const props = defineProps<{ topicId: string; profile: TopicComputeProfile }>()
const emit = defineEmits<{ changed: [] }>()
const saving = ref(false)
const error = ref('')
// 选择变成了一条提议：这次点击没有改掉任何东西，等人点头。不是错误，所以不走
// `error` 那一行红字。
const proposal = ref('')
// 原来那台够不着、推不上去：人唯一可以不推就换的情况，这时把「仍然更换」给他，
// 并记住他刚选的是哪一台。
const unreachable = ref<ComputeChoice | null>(null)
const menuOpen = ref(false)
const more = ref(false)
const choices = computed(() => compactChoices(props.profile.project_default, props.profile.choice))
const cloudAvailable = computed(() => props.profile.profiles.some((p) => p.id === 'cloud' && p.available))
function online(choice: ComputeChoice): string {
  if (!choice.device_id) return ''
  const device = props.profile.devices.find((d) => d.device_id === choice.device_id)
  if (!device) return t('work.roomMachine.removed')
  return device.online ? t('work.roomMachine.online') : t('work.roomMachine.offline')
}
async function pick(choice: ComputeChoice, abandonUnpushed = false) {
  saving.value = true
  error.value = ''
  proposal.value = ''
  unreachable.value = null
  try {
    const saved = await setTopicComputeChoice(props.topicId, choice, abandonUnpushed ? { abandonUnpushed } : {})
    // 变提议时房间这一项没有变 —— 不说话就等于这次点击石沉大海。菜单留着不收，
    // 那句话就在他刚按下的那个控件上。
    if (saved.proposal) {
      proposal.value = saved.proposal.content
      return
    }
    menuOpen.value = false
    more.value = false
    emit('changed')
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.roomMachine.saveFailed')
    if (e instanceof ApiError && e.code === 'WorkComputerUnreachable') unreachable.value = choice
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <v-menu v-model="menuOpen" location="bottom end" :close-on-content-click="false">
    <template #activator="{ props: menuProps }">
      <button type="button" class="cp-action" v-bind="menuProps">{{ t('work.roomMachine.edit') }}</button>
    </template>
    <v-card class="cp-menu">
      <div class="cp-heading">{{ t('work.roomMachine.heading') }}</div>
      <p class="cp-hint">{{ t('work.roomMachine.hint') }}</p>
      <button
        v-for="choice in choices"
        :key="choiceKey(choice)"
        type="button"
        class="cp-row"
        :disabled="saving"
        @click="pick(choice)"
      >
        <v-icon size="18">{{
          choiceKey(choice) === choiceKey(profile.choice) ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank'
        }}</v-icon>
        <div class="cp-body">
          <div class="cp-label">
            {{ choiceName(choice) }}
            <span v-if="choiceKey(choice) === choiceKey(profile.project_default)" class="cp-badge">{{
              t('work.roomMachine.projectDefault')
            }}</span>
          </div>
          <div class="cp-hint">
            {{ choiceDetail(choice) }}<span v-if="online(choice)"> · {{ online(choice) }}</span>
          </div>
        </div>
      </button>
      <button type="button" class="cp-row" :disabled="saving" @click="more = !more">
        <v-icon size="18">{{ more ? 'mdi-chevron-up' : 'mdi-chevron-right' }}</v-icon>
        {{ t('work.roomMachine.more') }}
      </button>
      <ComputeChoiceForm
        v-if="more"
        :devices="profile.devices"
        :cloud-available="cloudAvailable"
        :cloud-vm-available="profile.cloud_vm_available"
        :busy="saving"
        @select="pick"
      />
      <p v-if="proposal" role="status" class="cp-proposal">{{ proposal }}</p>
      <p v-if="error" role="alert" class="cp-error">{{ error }}</p>
      <button
        v-if="unreachable"
        type="button"
        class="cp-row cp-abandon"
        data-testid="room-machine-abandon"
        :disabled="saving"
        @click="pick(unreachable, true)"
      >
        {{ t('work.roomMachine.abandon') }}
      </button>
    </v-card>
  </v-menu>
</template>

<style scoped>
/* 名册里行尾那颗小动作，和「更换」同一档。 */
.cp-action {
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
.cp-action:hover {
  color: var(--ink);
}
.cp-menu {
  width: 420px;
  max-width: calc(100vw - 32px);
  max-height: 70vh;
  overflow-y: auto;
  padding: 8px;
}
.cp-heading {
  padding: 8px;
  font-size: 14px;
  font-weight: 600;
}
.cp-hint {
  margin: 4px 8px 8px;
  color: var(--muted);
  font-size: 13px;
}
.cp-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 12px 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  text-align: left;
  font-size: 13px;
  cursor: pointer;
}
.cp-row:hover {
  background: var(--fill);
}
.cp-row:disabled {
  cursor: wait;
}
.cp-body {
  flex: 1;
  min-width: 0;
}
.cp-body .cp-hint {
  margin: 4px 0 0;
}
.cp-label {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.cp-badge {
  display: inline-block;
  padding: 0 4px;
  background: var(--fill);
  color: var(--muted);
  border-radius: var(--radius-sm);
  font-size: 13px;
  white-space: nowrap;
}
.cp-error {
  padding: 8px;
  color: var(--danger-ink);
  font-size: 13px;
}
.cp-abandon {
  color: var(--danger-ink);
}
.cp-proposal {
  padding: 8px;
  color: var(--warn-ink);
  font-size: 13px;
}
</style>
