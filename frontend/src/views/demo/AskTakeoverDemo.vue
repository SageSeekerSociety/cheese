<script setup lang="ts">
// 提问接管输入框：一页能点的演示，**按真实聊天栏的尺寸与结构摆**。
//
// 外框模仿 ChatPanel：一列 flex、高度固定，上面是对话区、下面是输入那一格。
// 演示的重点是那一格——有题要答时它里面画的是提问面板而不是 composer，两者互斥。
// 数据是假的，交互是真的（选项、方向键、Enter、180ms 那一下、Skip、Esc 都在）。
import type { AskGroupAction, AskGroupState } from '../../lib/askGroupState'

import { computed, reactive, ref } from 'vue'

import { t } from '../../i18n'
import { emptyAskDraft } from '../../lib/askState'

import AskGroupFlow from '../../components/ask/AskGroupFlow.vue'
import RoomComposer from '../../components/room/RoomComposer.vue'

const props = defineProps<{
  questions?: number
  /** 一上来就是收起态（看「有 N 个问题待回答」那一条长什么样）。 */
  dismissed?: boolean
}>()

const VIEWER = 'alice'

const members = Array.from({ length: props.questions ?? 2 }, (_, i) => `q${i + 1}`)
const scope = { topic_id: 'demo-room', asked_by: 'agent', id: 'demo-group', members, total: members.length }

const PROMPTS: Array<{ header: string; question: string; options: Array<{ text: string; explain?: string }> }> = [
  {
    header: '改动的范围',
    question: '这次改动要不要顺手把旧的预览一起换掉？',
    options: [
      { text: '一起换掉', explain: '旧预览留着会让人看错版本' },
      { text: '先留着', explain: '等新预览验证过再说' },
    ],
  },
  {
    header: '预览的归属',
    question: '新预览挂在哪个话题下比较合适？',
    options: [
      { text: '挂在当前话题', explain: '接着这次讨论看结果' },
      { text: '新开一个话题', explain: '不和这次讨论混在一起' },
    ],
  },
]

const state = reactive<AskGroupState>({
  scope,
  anchor: members[0]!,
  pending: null,
  busy: false,
  fresh: true,
  error: null,
  storageBlocked: false,
  confirm: false,
  conflict: false,
  unavailable: false,
  forms: Object.fromEntries(
    members.map((id) => [
      id,
      {
        draft: emptyAskDraft(),
        pending: null,
        editing: false,
        busy: false,
        fresh: true,
        saved: false,
        error: null,
        conflict: false,
        storageBlocked: false,
      },
    ])
  ),
  data: {
    group: scope as never,
    settlement: null,
    receipt: null,
    blocks: members.map((id, index) => {
      const prompt = PROMPTS[index % PROMPTS.length]!
      return {
        id,
        topic_id: 'demo-room',
        kind: 'message' as const,
        author_type: 'participant' as const,
        author: 'agent',
        content: prompt.question,
        created_at: '2026-10-03T00:00:00Z',
        // `asked` 必须是当前 viewer，否则面板判成「在等别人答」、不给选项。
        meta: { header: prompt.header, asked: VIEWER, options: prompt.options, allow_other: true },
      }
    }) as never,
  },
} as never)

const draft = ref('')
const hidden = ref(!!props.dismissed)
const settled = ref(false)
const needsAnswer = computed(
  () => !settled.value && !!state.data?.blocks.some((b) => !b.meta?.answer_log?.length)
)
const takeover = computed(() => (!hidden.value && needsAnswer.value ? state : null))
const waiting = computed(() => (hidden.value ? (state.data?.blocks.length ?? 0) : 0))
const hint = computed(() => t('ask.group.returnHint', { count: waiting.value }))

function onAction(action: AskGroupAction) {
  // 照产品里 useAskGroups 的做法把起草写回——不写回的话，点选项后
  // `currentReady` 永远是假、面板不会前进，这个演示就没法验行为。
  if (action.type === 'question') {
    const form = state.forms[action.blockId]
    if (form && action.action.type === 'draft') form.draft = action.action.draft
    return
  }
  if (action.type === 'later') {
    const form = state.forms[action.blockId]
    if (form) form.draft = { ...form.draft, later: true }
    return
  }
  if (action.type === 'submit') settled.value = true
}
function reset() {
  settled.value = false
  hidden.value = !!props.dismissed
}
</script>

<template>
  <div class="takeover-demo">
    <!-- 一个「聊天栏」：上面对话区、下面输入那一格，和产品里同构。 -->
    <div class="takeover-demo__room">
      <div class="takeover-demo__timeline">
        <p class="takeover-demo__msg takeover-demo__msg--agent">芝士：改完了，你看一眼。</p>
        <p class="takeover-demo__msg">{{ settled ? '（已答完）' : '（对话区：这里会随输入那一格的高度让位）' }}</p>
      </div>

      <div class="takeover-demo__slot">
        <button v-if="waiting > 0" type="button" class="takeover-demo__return" @click="hidden = false">
          {{ hint }}
        </button>

        <AskGroupFlow
          v-if="takeover"
          :state="takeover"
          :viewer="VIEWER"
          :names="{}"
          composer
          auto-focus
          @action="onAction"
          @dismiss="hidden = true"
        />

        <div v-else class="takeover-demo__composer">
          <span>{{ settled ? '题答完了，输入框自己回来了（没点过任何「关闭」）。' : '没有题要答，这里就是平常的输入框。' }}</span>
          <span class="takeover-demo__caret" aria-hidden="true">|</span>
        </div>
      </div>
    </div>

    <button type="button" class="takeover-demo__reset" @click="reset">再来一次</button>

    <!-- 参照：同一个宽度下，产品里那个真输入框长什么样。上面那一格如果比它窄，
         就是「没有拉伸」。 -->
    <div class="takeover-demo__ref">
      <p class="takeover-demo__reflabel">参照：这一列里的真输入框</p>
      <RoomComposer
        v-model="draft"
        :topic="null"
        :mention-pool="[]"
        :topic-list="[]"
        :agent-seat="null"
        agent-name="芝士"
        :always-summon="false"
        hint="输入消息，@芝士 交给它处理"
        :atts="[]"
        :atts-uploading="false"
      />
    </div>
  </div>
</template>

<style scoped>
.takeover-demo {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
/* 和真实聊天栏一样：一列 flex、定高，让下面那一格的高度变化能从上面那一格看出来。 */
.takeover-demo__room {
  display: flex;
  flex-direction: column;
  height: min(720px, 76vh);
  max-width: 820px;
  border: 1px dashed var(--line-2);
  border-radius: 12px;
  overflow: hidden;
  background: var(--bg);
}
.takeover-demo__timeline {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.takeover-demo__msg {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}
.takeover-demo__msg--agent {
  color: var(--text);
}
.takeover-demo__slot {
  flex: none;
}
.takeover-demo__return {
  align-self: flex-start;
  margin: 0 16px 6px;
  border: 1px solid var(--line-2);
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 13px;
  background: transparent;
  cursor: pointer;
}
.takeover-demo__composer {
  margin: 0 16px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 10px 14px;
  color: var(--muted);
  font-size: 14px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  background: var(--surface);
}
.takeover-demo__caret {
  opacity: 0.4;
}
.takeover-demo__ref {
  max-width: 820px;
  border: 1px dashed var(--line-2);
  border-radius: 12px;
  padding: 8px 0;
}
.takeover-demo__reflabel {
  margin: 0 16px 6px;
  font-size: 12px;
  color: var(--muted);
}
.takeover-demo__reset {
  align-self: flex-start;
  font-size: 13px;
  text-decoration: underline;
  background: transparent;
  border: 0;
  cursor: pointer;
  opacity: 0.7;
}
</style>
