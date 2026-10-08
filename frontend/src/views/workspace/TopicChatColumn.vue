<script setup lang="ts">
import type { AgentControlState, Block, ChatAttachment, ProjectMemberRow, Topic } from '@/cx_types'
import type { DocReviewRequest } from '@/lib/docReview'
import type { OpenedDocument } from '@/lib/docReview'
import type { MemberActivityLine } from '@/lib/memberActivity'
import type { CardPhase } from '@/lib/topicState'
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { computed, onMounted, ref, toRef, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useSkillProposals } from './useSkillProposals'

import BaseButton from '@/components/base/BaseButton.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import AgentFeedbackCard from '@/components/feedback/AgentFeedbackCard.vue'
import SkillProposalCard from '@/components/room/SkillProposalCard.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'
import { useWorkspaceStore } from '@/stores/workspace'

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
  /** 这一栏是房间里一个任务的对话：每句话都说给做它的 AI 队友，采纳卡是这个任务的，
   *  房间才有的提议卡（技能、任务、反馈）不在这里。 */
  taskId?: string | null
  /** 这里此刻不能说话的原因，见 ChatPanel。 */
  composerClosed?: string | null
  /** 采纳那一条此刻挂在别处（专注模式里它在面板底部），这里不再放一份。 */
  acceptElsewhere?: boolean
  /** 手机上：采纳那一条只放「审阅」，决定在「改动」页底部。 */
  acceptReviewButton?: boolean
  // 换过 AI 队友之后 +1，对话栏据此重拉名册（它显示的 AI 名字来自那份名册）。
  // 同样必须一路透传：漏掉它不报错，只是换完队友对话里还写着上一个的名字。
}>()

const emit = defineEmits<{
  (e: 'open-room'): void
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
  // `id` 是后端指名的那一行（topics 帧上是房间 id）；两个都要转，漏掉的话刷新又退回整份重下。
  (e: 'state-changed', resource: string, id?: string): void
  // 芝士摆出来一份东西：面板立刻看一眼当前预览。必须一路透传，漏掉的话「预览」
  // 那一格又回到等轮询。
  (e: 'preview-shown'): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  // 参数都要转：`turnId` 决定文档面板高亮哪一轮改的段落，`review` 是「查看改动」要标出
  // 的那几处；只转第一个的话这两样都会静默降级成「整篇闪一下」。
  (e: 'open-resource', resource: string, turnId?: string, review?: DocReviewRequest, document?: OpenedDocument): void
  (e: 'upgrade-message', payload: unknown): void
  // 「在支线中回复」、点开消息下面那一行：页面换到那条支线。
  (e: 'open-thread', block: Block): void
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
const feedbackRef = ref<{ reload: () => Promise<void> } | null>(null)
// 正在写退回理由：输入框让给退回那一块（AcceptRejectForm），写到一半的消息留在原处。
const rejecting = ref(false)

const router = useRouter()
// 卡片属于提出它的那段对话：在任务里就是这个任务，否则是房间本身。三种卡都按它
// 取、按它决定 —— 任务里提的卡落在任务里，按房间去取就一张也看不到。
const conversationId = computed(() => props.taskId ?? props.topic.id)
// 技能的提议卡：取数在这里（组件下不许取数），卡片只画。换对话就重读。
const skills = useSkillProposals(
  toRef(() => props.topic.project_id),
  conversationId
)
onMounted(skills.load)
watch(conversationId, skills.load)
function openSkill(skill: { id: string }) {
  void router.push({
    name: 'project-skills',
    params: { projectId: props.topic.project_id },
    query: { skill: skill.id },
  })
}

const store = useWorkspaceStore()

// 没加入的频道：输入框的位置是一句话加「加入频道」，加入之后输入框回来。
const joining = ref(false)
async function join() {
  joining.value = true
  try {
    await store.setJoined(props.topic.id, true)
  } finally {
    joining.value = false
  }
}

const connected = computed(() => !!chatRef.value?.connected)
const submitQuestion: SubmitPreviewQuestion = (request) => chatRef.value?.submitQuestion(request) ?? false

defineExpose({
  connected,
  reloadFeedback: () => feedbackRef.value?.reload(),
  reloadSkills: () => skills.load(),
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
      :conversation-id="taskId"
      :composer-closed="composerClosed"
      :always-summon="!!taskId"
      hide-header
      :show-composer="!rejecting"
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
      @state-changed="(resource: string, id?: string) => emit('state-changed', resource, id)"
      @preview-shown="emit('preview-shown')"
      @mention-click="emit('mention-click', $event)"
      @open-file="(path) => emit('open-file', path)"
      @open-resource="
        (resource: string, turnId?: string, review?: DocReviewRequest, document?: OpenedDocument) =>
          emit('open-resource', resource, turnId, review, document)
      "
      @upgrade-message="emit('upgrade-message', $event)"
      @open-thread="emit('open-thread', $event)"
      @open-topic="emit('open-topic', $event)"
      @open-card="emit('open-card', $event)"
    >
      <!-- 验收卡贴在输入框上方，不在时间线末尾：它是一个等人做的决定，要一直看得
           见，但平时只占一行，不把对话挤到只剩几行。 -->
      <template #above-composer>
        <TopicAcceptCard
          v-if="!acceptElsewhere"
          class="chat-dock"
          :topic-id="topic.id"
          :task-id="taskId ?? undefined"
          :topic-status="topic.status"
          :review-button="acceptReviewButton"
          @phase="emit('phase', $event)"
          @review="emit('review')"
          @rejecting="rejecting = $event"
        />
      </template>
      <template #timeline-end>
        <!-- Agent 反馈卡：「这一轮结束时，平台要人做的一个决定」，接在这一轮的
             对话后面。
             什么时候出现由**服务端**说了算：它列出这个话题里还活着的提案卡
             （`GET /topics/{id}/feedback-proposals`），一张都没有就什么都不画。
             「不用」记在服务端（按指纹），所以拒绝过一次的问题不会因为刷新又回来；
             换个说法重提的会回来 —— 那是另一次提问，值得再问一遍。 -->
        <AgentFeedbackCard ref="feedbackRef" :topic-id="conversationId" />
        <!-- 技能提议卡：芝士把一套做法整理好了，请人就地决定存不存。同样由服务端
             说了算：列的是这个房间里还在等人的提议，没有就什么都不画。 -->
        <SkillProposalCard
          :proposals="skills.proposals.value"
          :saved="skills.saved.value"
          :busy="skills.busy.value"
          :error="skills.error.value"
          @save="skills.save"
          @decline="skills.decline"
          @open="openSkill"
        />
      </template>
      <!-- 输入区那一行只放**这条消息**的动作，所以这里只剩话题的状态。谁在跑
         （AI 队友）和在哪跑（工作电脑）都不是某条消息的动作，摆在输入区上纯是占
         位置：队友进了成员名册（它本来就是这个房间的成员），工作电脑在话题头的 ⋯ 里。 -->
      <template #composer-closed>
        <button v-if="taskId" type="button" class="back-to-room" @click="emit('open-room')">
          {{ t('work.task.backToRoom', { room: topicTitle(topic) }) }}
        </button>
        <BaseButton
          v-else-if="topic.joined === false && topic.status !== 'archived'"
          kind="primary"
          size="sm"
          :loading="joining"
          @click="join"
        >
          {{ t('work.channel.join') }}
        </BaseButton>
      </template>
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
.back-to-room {
  flex: none;
  padding: 0;
  border: 0;
  background: none;
  color: var(--text);
  cursor: pointer;
}
.back-to-room:hover {
  color: var(--ink);
  text-decoration: underline;
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
