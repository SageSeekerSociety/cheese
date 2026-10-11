<script setup lang="ts">
// 房间这一项：这个话题在哪台工作电脑上跑。一个话题一个容器（2026-09-28，推翻结论
// 60）：改它就是整个房间一起搬，房间里的每个 AI 队友都换过去。挂在成员名册里房间
// 那一行上。给的是任务的 id 时改的是那个任务自己的那一项，只有它的会话搬；机器访问
// 范围是房间的，任务这里不给改。
//
// 只吃 props、只往上发事件（`frontend_grade.py` 的 A 级）：发请求落在
// `composables/useTopicComputeChoice.ts` 里，由调用它的那一层接线，把 `saving` /
// `error` / `proposal` / `savedVersion` 递下来、把 `select` 接住（组件不取数，
// `.claude/rules/architecture.md`）。这里只剩本地要展开哪一张菜单、要不要展开那份
// 表单 —— 以及收到成功回执（`savedVersion`）时把它们收起来。
import type { ComputeChoice, TopicComputeProfile } from '../types/compute'

import { computed, ref, watch } from 'vue'

import { t } from '../i18n'
import { choiceDetail, choiceKey, choiceName, compactChoices } from '../lib/computeConfig'
import { renderNoticeMessage } from '../lib/noticeText'

import ComputeChoiceForm from './ComputeChoiceForm.vue'

const props = defineProps<{
  profile: TopicComputeProfile
  /** 一次保存还在路上：每一行都灰着，再点也没有用。 */
  saving: boolean
  /** 上一次选择被后端拒了的原话（不是提议）。 */
  error: string
  /** 上一次选择变成了一条提议：等人点头，房间没变。不是错误。 */
  proposal: string
  /** 成功回执：真的存成一次就涨一版，据此收菜单、收起展开的表单。 */
  savedVersion: number
}>()
const emit = defineEmits<{ select: [choice: ComputeChoice, visibility?: 'host' | 'isolated'] }>()
// 任务的那一份带 `follows_room`，房间的没有。
const isTask = computed(() => props.profile.follows_room != null)
const menuOpen = ref(false)
const more = ref(false)
// 存成一次（不是提议）父层把回执涨一版：收菜单、收起展开的表单。变成提议和失败都不
// 动它 —— 菜单留着，让那句话停在他刚按下的控件上，能再挑一次。
watch(
  () => props.savedVersion,
  () => {
    menuOpen.value = false
    more.value = false
  }
)
const choices = computed(() => compactChoices(props.profile.project_default, props.profile.choice))
const cloudAvailable = computed(() => props.profile.profiles.some((p) => p.id === 'cloud' && p.available))
function online(choice: ComputeChoice): string {
  if (!choice.device_id) return ''
  const device = props.profile.devices.find((d) => d.device_id === choice.device_id)
  if (!device) return t('work.roomMachine.removed')
  return device.online ? t('work.roomMachine.online') : t('work.roomMachine.offline')
}
// 房间在自己登记的那台机器上能看到什么：隔离环境（默认），或者整台机器——只有
// 机主本人能给。说的是房间点了名、或者第一轮已经钉下的那一台。
const machine = computed(() => {
  if (isTask.value || props.profile.current !== 'device') return null
  const id = props.profile.device_id ?? props.profile.choice.device_id
  return props.profile.devices.find((d) => d.device_id === id) ?? null
})
const access = computed(() => props.profile.visibility.effective)
const isolationUnavailable = computed(() =>
  machine.value?.sandbox_unavailable ? renderNoticeMessage(machine.value.sandbox_unavailable, '') : ''
)
function setAccess(visibility: 'host' | 'isolated') {
  const device = machine.value
  if (!device || visibility === access.value) return
  // Naming the machine is part of giving a room access to it.
  const choice: ComputeChoice = props.profile.choice.device_id
    ? props.profile.choice
    : { ...props.profile.choice, profile: 'device', name: device.name, device_id: device.device_id }
  emit('select', choice, visibility)
}
// 选了哪一台（或者给不给整台机器）往上发；保存、回执、重取都在父层那一侧。
function pick(choice: ComputeChoice, visibility?: 'host' | 'isolated') {
  emit('select', choice, visibility)
}
</script>

<template>
  <v-menu v-model="menuOpen" location="bottom end" :close-on-content-click="false">
    <template #activator="{ props: menuProps }">
      <button type="button" class="cp-action" v-bind="menuProps">{{ t('work.roomMachine.edit') }}</button>
    </template>
    <v-card class="cp-menu">
      <div class="cp-heading">{{ isTask ? t('work.roomMachine.taskHeading') : t('work.roomMachine.heading') }}</div>
      <p class="cp-hint">{{ isTask ? t('work.roomMachine.taskHint') : t('work.roomMachine.hint') }}</p>
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
      <template v-if="machine">
        <div class="cp-heading cp-access">{{ t('work.roomMachine.accessHeading', { name: machine.name }) }}</div>
        <button
          type="button"
          class="cp-row"
          data-testid="room-machine-isolated"
          :disabled="saving || !!isolationUnavailable"
          @click="setAccess('isolated')"
        >
          <v-icon size="18">{{ access === 'isolated' ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank' }}</v-icon>
          <div class="cp-body">
            <div class="cp-label">{{ t('work.roomMachine.isolated') }}</div>
            <div class="cp-hint">{{ isolationUnavailable || t('work.roomMachine.isolatedHint') }}</div>
          </div>
        </button>
        <button
          type="button"
          class="cp-row"
          data-testid="room-machine-host"
          :disabled="saving || !machine.owned"
          @click="setAccess('host')"
        >
          <v-icon size="18">{{ access === 'host' ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank' }}</v-icon>
          <div class="cp-body">
            <div class="cp-label">{{ t('work.roomMachine.host') }}</div>
            <div class="cp-hint">
              {{ machine.owned ? t('work.roomMachine.wholeMachineNotice') : t('work.roomMachine.hostOwnerOnly') }}
            </div>
          </div>
        </button>
      </template>
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
.cp-access {
  margin-top: 8px;
  border-top: 1px solid var(--line);
  padding-top: 12px;
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
.cp-proposal {
  padding: 8px;
  color: var(--warn-ink);
  font-size: 13px;
}
</style>
