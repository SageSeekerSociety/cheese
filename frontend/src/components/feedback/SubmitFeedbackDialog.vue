<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'

import { SPLIT_MIN_WIDTH } from '@/composables/useWorkspaceLayout'

import SubmitFeedbackForm from './SubmitFeedbackForm.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { useFeedbackStore } from '@/stores/feedback'

// 提交反馈的**对话框壳**。只有会话里那张 agent 提案卡用它，理由见组件里那份表单的
// 文件头：从对话里跳走会把「我刚看到的那张卡」留在身后，而卡片提交完要就地翻成一张
// 凭证（`AgentFeedbackCard` 的 `submitted` 是组件内的 ref，跳页必丢）。
//
// 字段一份都没有 —— 全在 `SubmitFeedbackForm` 里。这个文件只管对话框本身：宽度、
// 窄屏怎么办、关闭。
//
// **窄屏整屏**：表单在手机上本来就占满一屏，套一层四周留边的浮层只是把可读宽度再
// 减掉一圈，还多出「后面那层对话在动」的干扰。
//
// 「窄屏」的判据是 `$bp-phone`（< 768，`SPLIT_MIN_WIDTH`），不是 Vuetify 的 `xs`：
// 600 不是共享档位里的任何一个数，而且 600–767 这一段本来就是手机摆法（列表和房间
// 各占一整页），整屏才是对的。宽度量不出来时按手机算，和 `useWorkspaceLayout` 一样。
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void; (e: 'submitted', id: string): void }>()

const store = useFeedbackStore()
const { width } = useDisplay()
const fullscreen = computed(() => (width?.value ?? 0) < SPLIT_MIN_WIDTH)
const { t } = useI18n()

const fromProposal = computed(() => !!store.draft.proposal)

/** 关掉不是「不要了」：`closeSubmit` 会把防抖窗口里那几下收尾写下去。 */
function close() {
  store.closeSubmit()
  emit('update:open', false)
}

function onSubmitted(id: string) {
  emit('update:open', false)
  emit('submitted', id)
}
</script>

<template>
  <v-dialog
    :model-value="props.open"
    :fullscreen="fullscreen"
    max-width="720"
    scrollable
    @update:model-value="(value: boolean) => !value && close()"
  >
    <v-card rounded="lg" class="sfd-card">
      <div class="sfd-head">
        <span class="t-title">
          {{ fromProposal ? t('feedback.submit.titleFromAgent') : t('feedback.submit.title') }}
        </span>
        <v-spacer />
        <BaseButton icon="mdi-close" size="sm" :aria-label="t('feedback.submit.cancel')" @click="close" />
      </div>

      <v-card-text class="sfd-body">
        <SubmitFeedbackForm shell="dialog" @submitted="onSubmitted" @cancel="close" />
      </v-card-text>
    </v-card>
  </v-dialog>
</template>

<style scoped>
/* 头一行钉在顶上、正文自己滚：这一份表单长到一屏装不下，而关闭按钮要在人翻到任何
   一页时都够得着。 */
.sfd-card {
  display: flex;
  flex-direction: column;
  max-height: 90vh;
}
.sfd-head {
  display: flex;
  flex: none;
  align-items: center;
  padding: 12px 8px 12px 24px;
  border-bottom: 1px solid var(--line);
}
.sfd-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 24px 24px;
}
</style>
