<script setup lang="ts">
import type { AgentControlState, Block, ChatAttachment, ProjectMemberRow, Topic } from '@/cx_types'
import type { DocReviewRequest } from '@/lib/docReview'
import type { MemberActivityLine } from '@/lib/memberActivity'
import type { CardPhase } from '@/lib/topicState'
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { computed, ref } from 'vue'

import ChatPanel from '@/components/ChatPanel.vue'
import AgentFeedbackCard from '@/components/feedback/AgentFeedbackCard.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import { t } from '@/i18n'

// 话题的对话那一半：时间线 + 输入框 + 末尾的采纳框 + 输入框旁边的 chips。
//
// 它是一个组件而不是 TopicView 模板里的一段，只因为它要挂在两个地方：桌面上是
// 左边那一栏，手机上是工作面板 tab 栏里「对话」那一格的内容（一屏放不下两栏）。
// 同一份接线写两遍是这两处早晚长歪的原因，所以它只写一遍。
defineOptions({ name: 'TopicChatColumn' })

const props = defineProps<{
  topic: Topic
  members: ProjectMemberRow[]
  topicList: Topic[]
  // 开这个话题的那一刻还有多少条没读——对话栏用它画「以下是新消息」那条线。
  // 必须一路透传：漏掉它不会报错，只是那条线再也不出现。
  unreadOnOpen?: number
  /** 打开时停在这一条（地址里的 `?block=`）。 */
  focusBlock?: string | null
  // 换过 AI 队友之后 +1，对话栏据此重拉名册（它显示的 AI 名字来自那份名册）。
  // 同样必须一路透传：漏掉它不报错，只是换完队友对话里还写着上一个的名字。
}>()

const emit = defineEmits<{
  (e: 'turn-done'): void
  // 芝士 开工 / 收工。必须一路透传：右边那格「现场」靠它在开工那一刻出现。
  (e: 'working', working: boolean): void
  // 会话状态的那一帧。同样一路透传给现场那格的会话详情。
  (e: 'agent-control', state: AgentControlState): void
  // 现场时间线上新到或变了的一行、在跑的轮次各自的开始时间：一路透传给现场那格。
  // 漏掉不报错，只是现场又回到「打开才刷新」。
  (e: 'site-block', block: Block): void
  (e: 'site-turns', turns: Record<string, number>): void
  // 谁在这个房间里忙：现场那一格画同一份。
  (e: 'activity', lines: MemberActivityLine[]): void
  (e: 'state-changed', payload: unknown): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string, taskId?: string | null): void
  // 参数都要转：`turnId` 决定文档面板高亮哪一轮改的段落，`review` 是「查看改动」要标出
  // 的那几处；只转第一个的话这两样都会静默降级成「整篇闪一下」。
  (e: 'open-resource', resource: string, turnId?: string, review?: DocReviewRequest): void
  (e: 'upgrade-message', payload: unknown): void
  (e: 'open-topic', topicId: string): void
  (e: 'open-card', taskId: string): void
  // 话题此刻处在哪一段，由采纳框说了算——头部的状态词和面板开在哪一格都读它。
  (e: 'phase', phase: CardPhase): void
  // 采纳框上的「去看改动」：面板换到 改动 那一格。
  (e: 'review'): void
}>()

const chatRef = ref<{
  connected: boolean
  send: (content: string, summon: boolean, attachments?: ChatAttachment[]) => boolean
  submitQuestion: SubmitPreviewQuestion
} | null>(null)
const acceptRef = ref<{ reload: (silent?: boolean) => Promise<void> } | null>(null)
const feedbackRef = ref<{ reload: () => Promise<void> } | null>(null)

const connected = computed(() => !!chatRef.value?.connected)
const submitQuestion: SubmitPreviewQuestion = (request) => chatRef.value?.submitQuestion(request) ?? false

defineExpose({
  connected,
  reloadAccept: (silent?: boolean) => acceptRef.value?.reload(silent),
  reloadFeedback: () => feedbackRef.value?.reload(),
  // 普通定位沿用聊天提交；图上画过东西时随行带那张合成图。明确的整页 AI 提问由
  // submitQuestion 在正文点名。
  say: (content: string, attachments?: ChatAttachment[]) => chatRef.value?.send(content, true, attachments) ?? false,
  submitQuestion,
})
</script>

<template>
  <div class="chat-col">
    <ChatPanel
      ref="chatRef"
      :topic="topic"
      hide-header
      show-composer
      :members="members"
      :topic-list="topicList"
      :unread-on-open="unreadOnOpen"
      :focus-block="focusBlock"
      @turn-done="emit('turn-done')"
      @working="emit('working', $event)"
      @agent-control="emit('agent-control', $event)"
      @site-block="emit('site-block', $event)"
      @site-turns="emit('site-turns', $event)"
      @activity="emit('activity', $event)"
      @state-changed="emit('state-changed', $event)"
      @mention-click="emit('mention-click', $event)"
      @open-file="(path, taskId) => emit('open-file', path, taskId)"
      @open-resource="
        (resource: string, turnId?: string, review?: DocReviewRequest) =>
          emit('open-resource', resource, turnId, review)
      "
      @upgrade-message="emit('upgrade-message', $event)"
      @open-topic="emit('open-topic', $event)"
      @open-card="emit('open-card', $event)"
    >
      <!-- 验收卡贴在输入框上方，不在时间线末尾：它是一个等人做的决定，要一直看得
           见，但平时只占一行，不把对话挤到只剩几行。 -->
      <template #above-composer>
        <TopicAcceptCard
          ref="acceptRef"
          class="chat-dock"
          docked
          :topic-id="topic.id"
          :topic-status="topic.status"
          @phase="emit('phase', $event)"
          @review="emit('review')"
        />
      </template>
      <template #timeline-end>
        <!-- Agent 反馈卡：「这一轮结束时，平台要人做的一个决定」，接在这一轮的
             对话后面。
             什么时候出现由**服务端**说了算：它列出这个话题里还活着的提案卡
             （`GET /topics/{id}/feedback-proposals`），一张都没有就什么都不画。
             「不用」记在服务端（按指纹），所以拒绝过一次的问题不会因为刷新又回来；
             换个说法重提的会回来 —— 那是另一次提问，值得再问一遍。 -->
        <AgentFeedbackCard ref="feedbackRef" :topic-id="topic.id" />
      </template>
      <!-- 输入区那一行只放**这条消息**的动作，所以这里只剩话题的状态。谁在跑
         （AI 队友）和在哪跑（工作电脑）都不是某条消息的动作，摆在输入区上纯是占
         位置：队友进了成员名册（它本来就是这个房间的成员），工作电脑在话题头的 ⋯ 里。 -->
      <template #composer-chips>
        <span v-if="topic.status === 'archived'" class="d-inline-flex align-center ga-1 c-faint archived-chip">
          <span class="status-dot status-dot--muted" />{{ t('work.sidebar.archived') }}
        </span>
      </template>
    </ChatPanel>
  </div>
</template>

<style scoped>
.archived-chip {
  font-size: 12px;
}
/* 和对话同一栏：时间线（ChatTimeline）、输入框（ChatPanel）各把自己收成一栏居中，
   贴在输入框上的这一条（验收卡）跟着收同一个值，不然它比上下两块都宽。桌面上是读
   的一栏 --page-w-read，手机外壳里是 --page-w，三块始终对齐。 */
.chat-dock {
  width: 100%;
  max-width: var(--page-w-read);
  margin-inline: auto;
}
@media (max-width: 959.98px) {
  .chat-dock {
    max-width: var(--page-w);
  }
}
.chat-col {
  /* 对话栏那条错误提示（ChatPanel 的 .chat-error-toast）是 absolute，定位的就是
     这一层——ChatPanel 自己的根不是定位元素。 */
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
</style>
