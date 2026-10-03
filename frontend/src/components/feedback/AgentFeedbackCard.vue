<script setup lang="ts">
import type { FeedbackProposal } from '@/cx_types'

import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import FeedbackAuthorAvatar from './FeedbackAuthorAvatar.vue'
import { kindLabel } from './feedbackLabels'
import SubmitFeedbackDialog from './SubmitFeedbackDialog.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'
import { useWorkspaceStore } from '@/stores/workspace'

// 会话里的 Agent 反馈卡：芝士排查完之后，在这里问一句「要提交反馈吗」。
//
// 它解决的问题是**上下文会丢**：问题是在对话里发现的，复现步骤、会话 ID、现场
// 日志都在对话里；让人另开一个页面重新打一遍，最有价值的那部分就没了。所以这张卡
// 自己带着三段现场，点「提交反馈」就地开一个对话框把它们递进去（**不跳页**：跳走会把
// 这张卡留在身后，而它提交完要就地翻成一张凭证）。
//
// 三颗按钮的位置是有讲究的，不是随便排的：
//   * 查看详情  —— 展开 What happened / Repro / Evidence。默认收起：不展开就先
//                  看三屏证据，人只会直接点「不用」。
//   * 不用      —— 「不用」必须和「提交反馈」**一样容易点到**。这是一张平台主动
//                  递上来的卡，如果拒绝它比接受它更费劲，那它就不叫「问一句」了。
//   * 提交反馈  —— 唯一的实心按钮。
//
// 这一轮它接上了真接口，所以三件事换了地方：
//   * 卡从哪来：`GET /topics/{id}/feedback-proposals`（`live_cards`）。数据是
//     `Block.meta.feedback_proposal`，不是本地写死的样例。
//   * 「不用」记在哪：**服务端**，按指纹记（`feedback_proposal_dismissals`）。
//     原型只是把这一帧的 state 改成 dismissed，刷新就回来 —— 而「我拒绝过这个」
//     恰恰是最需要跨刷新记住的一句话。
//   * 发出去的是哪条：走 `accept`（`POST .../{block_id}/accept`），作者从卡上取
//     （提案的 agent），提交者取调用者（我）。正文以表单里那份为准 —— 人要为自己
//     发出去的东西负责，所以他能改。
//
// 卡片正文里最显眼的那一块是**用户原话**（`user_said`）。服务端把它做成必填，
// 且只认两种填法：引用原话，或者那句「用户没有就这个问题说过话」。把这一条摆在
// 「判断依据」上面，是这张卡唯一一个不用解释就成立的诚信机制：读的人第一眼看到的
// 不是芝士的推理，而是这句话有没有根据。
defineOptions({ name: 'AgentFeedbackCard' })

const props = defineProps<{ topicId: string }>()

const store = useFeedbackStore()
const workspace = useWorkspaceStore()

/** 名册上的名字；名册里没有（还没加载、或者不在这个项目里）就退回 handle。 */
function nameOf(handle: string): string {
  return workspace.members.find((m) => m.user_handle === handle)?.name || handle
}
const router = useRouter()

/** 这个话题里还活着的提案。拉不到就是空数组（提案是顺路问一句，不该让对话栏报错）。 */
const proposals = ref<FeedbackProposal[]>([])
/** 已经发出去的：block_id → 那条反馈的 id。卡在这一轮里变成一张凭证。 */
const submitted = ref<Record<string, string>>({})
/** 已经「不用」的：本地立刻收起。服务端那边同一件事已经落库了。 */
const dismissed = ref<Set<string>>(new Set())
/** 展开着的那些（按 block_id）。 */
const expanded = ref<Set<string>>(new Set())
/** 提交表单正为哪张卡开着。表单是全局唯一的那一份，所以只需要记一个。 */
const pending = ref<string | null>(null)
/** 对话框开着没有。它和 `pending` 是两件事：`pending` 说的是「提交成功后算哪张卡的
 *  凭证」，关掉对话框要给那张卡留一句话，所以它得活到 `onSubmitted` 跑完。 */
const formOpen = ref(false)

/** 拉一次这个话题里还活着的卡。开着页面时，卡落下、被发出去、被「不用」，房间都会
 *  推一句「提案卡变了」（`TopicView` 收到后调这里），所以这不止在挂载时跑一次。
 *
 *  这里发出去的卡已经不在「还活着」的那份里了，但它此刻正翻成一张凭证摆在屏幕上 ——
 *  那张凭证留着，直到人离开这个页面。 */
async function load() {
  const live = await store.loadProposals(props.topicId)
  const sentHere = proposals.value.filter(
    (p) => submitted.value[p.block_id] && !live.some((l) => l.block_id === p.block_id)
  )
  proposals.value = [...live, ...sentHere]
}

onMounted(load)
defineExpose({ reload: load })

function visibleCards(): FeedbackProposal[] {
  return proposals.value.filter((p) => !dismissed.value.has(p.block_id))
}

function toggleExpanded(blockId: string) {
  const next = new Set(expanded.value)
  if (next.has(blockId)) next.delete(blockId)
  else next.add(blockId)
  expanded.value = next
}

function openForm(proposal: FeedbackProposal) {
  const p = proposal.payload
  pending.value = proposal.block_id
  store.openSubmit({
    kind: p.kind,
    title: p.title,
    body: p.problem,
    visibility: p.visibility,
    // 提案自己带的标签一起填进去：后端一直收 `tags`、也一直在提案上带着它，只是表单
    // 以前没这一栏。人可以在表单上改 —— 发出去的是他改过的那份。
    tags: p.tags ?? [],
    // 从这张卡进来时现场默认勾上：卡存在的理由就是别让现场丢掉。
    attachContext: true,
    fromAgent: {
      whatHappened: p.what_happened ?? '',
      repro: p.repro ?? '',
      evidence: p.evidence ?? '',
      sessionId: p.session_id ?? undefined,
      environment: p.environment ?? undefined,
    },
    proposal: { topicId: props.topicId, blockId: proposal.block_id },
  })
  formOpen.value = true
}

/** 「不用」。服务端按**指纹**记，所以同一个问题的另一种说法回来时是另一条，会被再问一次
 *  —— 那是刻意的：换过说法的那条，值得再问一遍。 */
function dismiss(proposal: FeedbackProposal) {
  dismissed.value = new Set([...dismissed.value, proposal.block_id])
  void store.dismissProposal(props.topicId, proposal.block_id)
}

/** 提交完成 —— 表单是全局共享的那一份，所以只由**开着它的那张卡**记下来。 */
function onSubmitted(id: string) {
  const blockId = pending.value
  pending.value = null
  if (!blockId) return
  submitted.value = { ...submitted.value, [blockId]: id }
}
</script>

<template>
  <template v-for="proposal in visibleCards()" :key="proposal.block_id">
    <div v-if="submitted[proposal.block_id]" class="fb-agent-card mt-2">
      <div class="fb-agent-card__pad">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon color="success" size="19">mdi-check-circle-outline</v-icon>
          <span class="t-title">{{ t('feedback.proposal.submitted') }}</span>
        </div>
        <div class="t-body mb-3">{{ t('feedback.proposal.progress') }}</div>
        <BaseButton kind="secondary" size="sm" @click="router.push(`/feedback/${submitted[proposal.block_id]}`)">
          {{ t('feedback.proposal.view') }}
        </BaseButton>
      </div>
    </div>

    <div v-else class="fb-agent-card mt-2">
      <div class="fb-agent-card__pad">
        <!-- 谁的判断只占一行小字：这句话每张卡都一样，反馈自己的标题才是这张卡唯一
             的标题。作者显示名册上的名字，不是 handle。这张卡只有一个作者，而且一定是
             agent（提案接口就是 agent 那条通道），所以 `is-agent` 直接写死，不按
             handle 去猜。 -->
        <div class="fb-agent-card__byline t-meta-read">
          <FeedbackAuthorAvatar
            :handle="proposal.author_handle"
            :name="nameOf(proposal.author_handle)"
            is-agent
            :size="20"
          />
          <!-- 一整段字，跟着宽度自然折行；拆成几块各自换行的话，窄的时候头像、
               这句话、类型和时间会各占一行。 -->
          <span
            >{{ t('feedback.proposal.byline', { name: nameOf(proposal.author_handle) }) }} ·
            {{ kindLabel(proposal.payload.kind) }} · {{ relTime(proposal.authored_at) }}</span
          >
        </div>

        <div class="t-title fb-agent-card__title">{{ proposal.payload.title }}</div>

        <!-- 用户原话。**放在判断依据上面**，理由见文件开头：这是这张卡唯一一个
             不用解释就成立的诚信机制，读的人该先看见它。 -->
        <div class="fb-agent-card__said mb-3">
          <div class="t-eyebrow mb-1">{{ t('feedback.proposal.userSaid') }}</div>
          <div class="t-body fb-agent-card__quote">{{ proposal.payload.user_said }}</div>
        </div>

        <div v-if="proposal.payload.why" class="fb-agent-card__reason mb-3">
          <div class="t-eyebrow mb-1">{{ t('feedback.proposal.why') }}</div>
          <div class="t-body fb-agent-card__text">{{ proposal.payload.why }}</div>
        </div>

        <!-- 展开区：三段现场。默认收起，见上面那段注释。
             高度用网格行从 0fr 过渡到 1fr，内容常驻在 DOM 里、收起时 inert。证据框的
             内边距、边框和下外边距都在被裁的那一层**里面**：放在外面的话，高度动画缩不
             过那 22px 的内边距加边框，外边距又根本不参与动画，展开第一帧就凭空多出
             34px、停住一下才开始长，收起时也停在 34px 再一下子消失。 -->
        <!-- `|| undefined`：inert 只看属性在不在，`inert="false"` 照样让整块读不到、点不了。 -->
        <div class="fb-agent-card__fold" :class="{ 'is-open': expanded.has(proposal.block_id) }">
          <div class="fb-agent-card__fold-inner" :inert="!expanded.has(proposal.block_id) || undefined">
            <div class="fb-agent-card__evidence">
              <div v-if="proposal.payload.what_happened" class="fb-evidence-block">
                <div class="t-eyebrow mb-1">{{ t('feedback.detail.whatHappened') }}</div>
                <div class="t-body fb-agent-card__text">{{ proposal.payload.what_happened }}</div>
              </div>
              <div v-if="proposal.payload.repro" class="fb-evidence-block">
                <div class="t-eyebrow mb-1">{{ t('feedback.detail.repro') }}</div>
                <pre class="fb-evidence-pre">{{ proposal.payload.repro }}</pre>
              </div>
              <div v-if="proposal.payload.evidence" class="fb-evidence-block">
                <div class="t-eyebrow mb-1">{{ t('feedback.detail.evidence') }}</div>
                <div class="t-body fb-agent-card__text">{{ proposal.payload.evidence }}</div>
              </div>
              <!-- 日志只给个长度，不铺开：它是最大的一段（上限两万字），而这一屏的
                 目的是让人决定要不要提交，不是读日志。真正的日志随反馈一起走。 -->
              <div v-if="proposal.payload.logs" class="fb-evidence-block">
                <div class="t-eyebrow mb-1">{{ t('feedback.proposal.logs') }}</div>
                <div class="t-meta">
                  {{ t('feedback.proposal.logsAttached', { n: proposal.payload.logs.length }) }}
                </div>
              </div>
              <div v-if="proposal.payload.session_id || proposal.payload.environment" class="t-meta">
                <template v-if="proposal.payload.session_id">
                  {{ t('feedback.sessionLine', { id: proposal.payload.session_id }) }}
                </template>
                <template v-if="proposal.payload.session_id && proposal.payload.environment"> · </template>
                <template v-if="proposal.payload.environment">{{ proposal.payload.environment }}</template>
              </div>
            </div>
          </div>
        </div>

        <div class="d-flex align-center flex-wrap ga-2">
          <BaseButton
            kind="ghost"
            size="sm"
            :prepend-icon="expanded.has(proposal.block_id) ? 'mdi-chevron-up' : 'mdi-chevron-down'"
            @click="toggleExpanded(proposal.block_id)"
          >
            {{ expanded.has(proposal.block_id) ? t('feedback.proposal.collapse') : t('feedback.proposal.expand') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" @click="dismiss(proposal)">
            {{ t('feedback.proposal.dismiss') }}
          </BaseButton>
          <v-spacer />
          <BaseButton kind="primary" size="sm" @click="openForm(proposal)">
            {{ t('feedback.proposal.submit') }}
          </BaseButton>
        </div>
      </div>
    </div>
  </template>

  <!-- 对话框挂在卡片外面：跟着卡片一起被条件渲染的话，点「提交反馈」到它出现之间会
       多一帧空档，看起来像没反应。 -->
  <SubmitFeedbackDialog v-model:open="formOpen" @submitted="onSubmitted" />
</template>

<style scoped>
/* 卡片自己画边框圆角，不用 VCard：VCard 默认 rounded="xl"(24px) 带 `!important`，
   而这一屏里的卡片该和平台别处的面一样是 --radius-lg、1px --line。颜色也不交给
   Vuetify 的那组主题变量 —— 这一页整块走 style.css 的令牌。 */
.fb-agent-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  color: var(--ink);
}
.fb-agent-card__pad {
  padding: 12px;
}
/* 谁的判断：头像、名字和这句话一行，类型和时间接在后面。 */
.fb-agent-card__byline {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 6px;
}
.fb-agent-card__title {
  margin-bottom: 10px;
}
/* 展开区：网格行 0fr → 1fr。内层裁掉溢出，间距（下外边距）在内层里面，所以跟着
   高度一起出现、一起消失。 */
.fb-agent-card__fold {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--dur-base) var(--ease-standard);
}
.fb-agent-card__fold.is-open {
  grid-template-rows: 1fr;
}
.fb-agent-card__fold-inner {
  min-height: 0;
  overflow: hidden;
}
.fb-agent-card__fold-inner > .fb-agent-card__evidence {
  margin-bottom: 12px;
}
@media (prefers-reduced-motion: reduce) {
  .fb-agent-card__fold {
    transition: none;
  }
}
/* 用户原话用左边一道竖线引用，判断依据用 inset 底色 —— 两件事不该长得一样：
   原话是**证据**（不可改写），判断依据是**推理**（可能错）。 */
.fb-agent-card__said {
  padding: 8px 10px 8px 12px;
  border-left: 2px solid var(--line-2);
}
.fb-agent-card__quote {
  font-style: italic;
  color: var(--text);
  white-space: pre-wrap;
}
.fb-agent-card__reason {
  padding: 8px 10px;
  border-radius: var(--radius-md);
  background: var(--fill);
}
.fb-agent-card__text {
  white-space: pre-wrap;
}
.fb-agent-card__evidence {
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.fb-evidence-block + .fb-evidence-block {
  margin-top: 10px;
}
.fb-evidence-pre {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
