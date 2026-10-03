<script setup lang="ts">
// 提问接管输入框：一页能点的演示。
//
// 它把产品里那件 `AskGroupFlow` 和「输入框那一格」摆在一起，按 ChatPanel 里同样
// 的二选一逻辑渲染：有题等我答就把 composer 换下来（不用点），答完或没有题时它
// 自己回来。Esc 收起之后靠「有 N 个问题待回答」那一条收回——问题不会被永久藏掉。
//
// 数据是假的，交互是真的：选项、方向键、Enter、那个 180ms 的提交前高亮、Skip、
// Esc，走的都是产品代码。这里只负责编一份 `AskGroupState` 和把提交/收起接住。
import type { AskGroupAction, AskGroupState } from '../../lib/askGroupState'

import { computed, reactive, ref } from 'vue'

import { t } from '../../i18n'
import { emptyAskDraft } from '../../lib/askState'

import AskGroupFlow from '../../components/ask/AskGroupFlow.vue'

const props = defineProps<{
  questions?: number
  /** 一上来就是收起态（看「有 N 个问题待回答」那一条长什么样）。 */
  dismissed?: boolean
}>()

const members = Array.from({ length: props.questions ?? 2 }, (_, i) => `q${i + 1}`)
const scope = { topic_id: 'demo-room', asked_by: 'agent', id: 'demo-group', members, total: members.length }

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
      { draft: emptyAskDraft(), pending: null, editing: false, busy: false, fresh: true, saved: false, error: null, conflict: false, storageBlocked: false },
    ])
  ),
  data: {
    group: scope as never,
    settlement: null,
    receipt: null,
    blocks: members.map((id, index) => ({
      id,
      topic_id: 'demo-room',
      kind: 'message' as const,
      author_type: 'participant' as const,
      author: 'agent',
      content: index === 0 ? '这次改动要不要顺手把旧的预览一起换掉？' : '新预览挂在哪个话题下比较合适？',
      created_at: '2026-10-03T00:00:00Z',
      meta: {
        header: index === 0 ? '改动的范围' : '预览的归属',
        options: [
          { text: '一起换掉', explain: '旧预览留着会让人看错版本' },
          { text: '先留着', explain: '等新预览验证过再说' },
        ],
        allow_other: true,
      },
    })) as never,
  },
} as never)

const hidden = ref(!!props.dismissed)
const settled = ref(false)
const needsAnswer = computed(() => !settled.value && state.data?.blocks.some((b) => !b.meta?.answer_log?.length))
const takeover = computed(() => (!hidden.value && needsAnswer.value ? state : null))
const waiting = computed(() => (hidden.value ? (state.data?.blocks.length ?? 0) : 0))

function onAction(action: AskGroupAction) {
  // 演示只需要「答完了」这一个转折：收到 submit 就把这一组结掉，输入框回来。
  if (action.type === 'submit') settled.value = true
}

const hint = computed(() =>
  t('ask.group.returnHint', { count: waiting.value })
)
function reset() {
  settled.value = false
  hidden.value = !!props.dismissed
}
</script>

<template>
  <div class="takeover-demo">
    <button v-if="waiting > 0" type="button" class="takeover-demo__return" @click="hidden = false">
      {{ hint }}
    </button>

    <AskGroupFlow
      v-if="takeover"
      :state="takeover"
      viewer="alice"
      :names="{}"
      composer
      auto-focus
      @action="onAction"
      @dismiss="hidden = true"
    />

    <div v-else class="takeover-demo__composer">
      <span>{{
        settled ? '题答完了，输入框自己回来了（没有点任何「关闭」）。' : '没有题要答，这里就是平常的输入框。'
      }}</span>
      <span class="takeover-demo__caret" aria-hidden="true">|</span>
    </div>

    <button type="button" class="takeover-demo__reset" @click="reset">
      {{ settled || hidden ? '再来一次' : '重置' }}
    </button>
  </div>
</template>

<style scoped>
.takeover-demo {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.takeover-demo__return {
  align-self: flex-start;
  border: 1px solid rgba(var(--v-border-color), 0.24);
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 13px;
  background: transparent;
  cursor: pointer;
}
.takeover-demo__composer {
  border: 1px solid rgba(var(--v-border-color), 0.24);
  border-radius: 20px;
  padding: 12px 16px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 14px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
}
.takeover-demo__caret {
  opacity: 0.4;
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
