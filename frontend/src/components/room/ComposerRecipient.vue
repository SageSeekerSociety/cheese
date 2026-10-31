<script setup lang="ts">
// 输入框顶上那一行：这条发出去交给谁。
//
// 它只画后端说的（`useAddressing`，发送时的同一套规矩），自己不判断。交给 AI 队友时画
// 那位的头像和名字；只 @ 了人时写那几个人；谁也不交给时写「本条：频道讨论」——频道默认
// 是人和人说话，这一行只是让人发之前看得见，不催人交给谁。任务里在前面写任务开着还是
// 关了：同一句话，任务一关就不再自动交给队友。
//
// 还没问到时（刚打开）这一行空着、但占着高度，问到了不会把输入框顶一下。
import type { Addressing } from '../../api/messages'

import { computed } from 'vue'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import { t } from '@/i18n'
import { teammateName } from '@/lib/agentNames'

const props = defineProps<{
  addressing: Addressing | null
  /** handle 在界面上叫什么（@ 到的人）。 */
  nameOf: (handle: string) => string
}>()

const task = computed(() => {
  const status = props.addressing?.task_status
  if (status === 'open') return t('work.room.composer.recipient.taskOpen')
  if (status === 'closed') return t('work.room.composer.recipient.taskClosed')
  return null
})
const people = computed(() =>
  (props.addressing?.people ?? []).map(props.nameOf).join(t('work.room.roster.listSeparator'))
)
const nobody = computed(() =>
  props.addressing?.place === 'channel'
    ? t('work.room.composer.recipient.channel')
    : t('work.room.composer.recipient.discussion')
)
// 后端存的是队友自己的名字（没人改过时是「芝士」，因为队友和提示词读的是它）；屏幕按读
// 的人的语言叫它，和名册、消息行同一条规矩（`teammateName`）。头像的读屏名也用这一个。
const agentName = computed(() => teammateName(props.addressing?.agent?.name, props.addressing?.agent?.name_source))
</script>

<template>
  <div class="recipient" aria-live="polite" :title="t('work.room.composer.recipient.label')">
    <template v-if="addressing">
      <template v-if="task">
        <span>{{ task }}</span>
        <span aria-hidden="true">{{ t('work.room.composer.recipient.sep') }}</span>
      </template>
      <template v-if="addressing.agent">
        <span>{{ t('work.room.composer.recipient.to') }}</span>
        <CheeseAvatar :size="16" :name="agentName" :handle="addressing.agent.handle" />
        <span class="recipient__name">{{ agentName }}</span>
      </template>
      <template v-else-if="people">
        <span>{{ t('work.room.composer.recipient.to') }}</span>
        <span class="recipient__name">{{ people }}</span>
      </template>
      <span v-else>{{ nobody }}</span>
    </template>
  </div>
</template>

<style scoped>
.recipient {
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 24px;
  padding: 4px 12px 0;
  overflow: hidden;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}
.recipient__name {
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text);
}
</style>
