<script setup lang="ts">
import type { ProjectMemberRow, Topic } from '@/cx_types'
import type { CardPhase } from '@/lib/topicState'

import { computed, ref } from 'vue'
import { useDisplay } from 'vuetify'

import ChatPanel from '@/components/ChatPanel.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import TopicComputePicker from '@/components/TopicComputePicker.vue'

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
  // 换过 AI 队友之后 +1，对话栏据此重拉名册（它显示的 AI 名字来自那份名册）。
  // 同样必须一路透传：漏掉它不报错，只是换完队友对话里还写着上一个的名字。
  rosterRevision?: number
}>()

const emit = defineEmits<{
  (e: 'turn-done'): void
  // 芝士 开工 / 收工。必须一路透传：右边那格「现场」靠它在开工那一刻出现。
  (e: 'working', working: boolean): void
  (e: 'state-changed', payload: unknown): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  // 两个参数都要转：`turnId` 决定文档面板高亮哪一轮改的段落，
  // 只转第一个的话那个功能会静默降级成「整篇闪一下」。
  (e: 'open-resource', resource: string, turnId?: string): void
  (e: 'upgrade-message', payload: unknown): void
  (e: 'open-topic', topicId: string): void
  // 话题此刻处在哪一段，由采纳框说了算——头部的状态词和面板开在哪一格都读它。
  (e: 'phase', phase: CardPhase): void
  // 采纳框上的「去看改动」：面板换到 改动 那一格。
  (e: 'review'): void
}>()

const { mdAndUp } = useDisplay()
// 算力是**房间**的选择，首轮就锁死；一条支线既改不了它，问它也 404。
const chatRef = ref<{ connected: boolean } | null>(null)
const acceptRef = ref<{ reload: (silent?: boolean) => Promise<void> } | null>(null)

const connected = computed(() => !!chatRef.value?.connected)

defineExpose({
  connected,
  reloadAccept: (silent?: boolean) => acceptRef.value?.reload(silent),
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
      :roster-revision="rosterRevision"
      @turn-done="emit('turn-done')"
      @working="emit('working', $event)"
      @state-changed="emit('state-changed', $event)"
      @mention-click="emit('mention-click', $event)"
      @open-file="emit('open-file', $event)"
      @open-resource="(resource: string, turnId?: string) => emit('open-resource', resource, turnId)"
      @upgrade-message="emit('upgrade-message', $event)"
      @open-topic="emit('open-topic', $event)"
    >
      <!-- 成果待采纳框，放在对话时间线末尾 (GitHub PR 的合并框样式) -->
      <template #timeline-end>
        <TopicAcceptCard
          ref="acceptRef"
          :topic-id="topic.id"
          :topic-status="topic.status"
          @phase="emit('phase', $event)"
          @review="emit('review')"
        />
      </template>
      <!-- 输入区那一行只放**这条消息**的动作，所以这里只剩话题的状态。谁在跑
         （AI 队友）和在哪跑（算力）都是话题级的设置，发第一条消息之后就不再变，
         摆在输入区上纯是占位置：队友进了成员名册（它本来就是这个房间的成员），
         算力见下面那块浮标 / 桌面的话题头。 -->
      <template #composer-chips>
        <span v-if="topic.status === 'archived'" class="d-inline-flex align-center ga-1 c-faint archived-chip">
          <span class="status-dot status-dot--muted" />已归档
        </span>
      </template>
    </ChatPanel>

    <!-- 手机上算力浮在对话上方：它是"这个话题在哪跑"，要一直看得见（整机权限
         尤其不能藏），但一行的高度在 390px 上太贵，所以它不占布局的高度。
         桌面上这块地方够宽，它长在话题头那一行里（TopicHeader）。 -->
    <div v-if="!mdAndUp" class="compute-float">
      <TopicComputePicker :key="topic.id" :topic-id="topic.id" />
    </div>
  </div>
</template>

<style scoped>
.archived-chip {
  font-size: 12px;
}
.chat-col {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
/* 浮标：不占布局高度，所以是 absolute。悬浮的东西才配有阴影（design-system
   §卡片不用阴影，菜单/对话框/抽屉这类浮层才用）。 */
.compute-float {
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2;
  display: flex;
  align-items: center;
  max-width: calc(100% - 24px);
  padding: 2px 8px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  box-shadow: var(--shadow-1);
}
</style>
