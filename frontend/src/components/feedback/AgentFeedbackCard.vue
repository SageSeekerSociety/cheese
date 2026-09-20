<script setup lang="ts">
import type { FeedbackProposal } from '@/cx_types'

import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import SubmitFeedbackDrawer from './SubmitFeedbackDrawer.vue'

import { KIND_LABEL } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 会话里的 Agent 反馈卡：芝士排查完之后，在这里问一句「要提交反馈吗」。
//
// 它解决的问题是**上下文会丢**：问题是在对话里发现的，复现步骤、会话 ID、现场
// 日志都在对话里；让人另开一个页面重新打一遍，最有价值的那部分就没了。所以这张卡
// 自己带着三段现场，点「提交反馈」直接把它们递进同一个抽屉。
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
//     （提案的 agent），提交者取调用者（我）。正文以抽屉里那份为准 —— 人要为自己
//     发出去的东西负责，所以他能改。
//
// 卡片正文里最显眼的那一块是**用户原话**（`user_said`）。服务端把它做成必填，
// 且只认两种填法：引用原话，或者那句「用户没有就这个问题说过话」。把这一条摆在
// 「判断依据」上面，是这张卡唯一一个不用解释就成立的诚信机制：读的人第一眼看到的
// 不是芝士的推理，而是这句话有没有根据。
defineOptions({ name: 'AgentFeedbackCard' })

const props = defineProps<{ topicId: string }>()

const store = useFeedbackStore()
const router = useRouter()

/** 这个话题里还活着的提案。拉不到就是空数组（提案是顺路问一句，不该让对话栏报错）。 */
const proposals = ref<FeedbackProposal[]>([])
/** 已经发出去的：block_id → 那条反馈的 id。卡在这一轮里变成一张凭证。 */
const submitted = ref<Record<string, string>>({})
/** 已经「不用」的：本地立刻收起。服务端那边同一件事已经落库了。 */
const dismissed = ref<Set<string>>(new Set())
/** 展开着的那些（按 block_id）。 */
const expanded = ref<Set<string>>(new Set())
/** 抽屉正为哪张卡开着的。抽屉是全局唯一的那一个，所以只需要记一个。 */
const pending = ref<string | null>(null)

async function load() {
  proposals.value = await store.loadProposals(props.topicId)
}

onMounted(load)
// 换话题就重拉：这个组件在话题之间会被复用（同一个路由，只换参数）。
watch(() => props.topicId, load)

function visibleCards(): FeedbackProposal[] {
  return proposals.value.filter((p) => !dismissed.value.has(p.block_id))
}

function toggleExpanded(blockId: string) {
  const next = new Set(expanded.value)
  if (next.has(blockId)) next.delete(blockId)
  else next.add(blockId)
  expanded.value = next
}

function openDrawer(proposal: FeedbackProposal) {
  const p = proposal.payload
  pending.value = proposal.block_id
  store.openSubmit({
    kind: p.kind,
    title: p.title,
    body: p.problem,
    visibility: p.visibility,
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
}

/** 「不用」。服务端按**指纹**记，所以同一个问题的另一种说法回来时是另一条，会被再问一次
 *  —— 那是刻意的：换过说法的那条，值得再问一遍。 */
function dismiss(proposal: FeedbackProposal) {
  dismissed.value = new Set([...dismissed.value, proposal.block_id])
  void store.dismissProposal(props.topicId, proposal.block_id)
}

/** 抽屉提交完成 —— 它是全局共享的那一个，所以只由**开着它的那张卡**记下来。 */
function onSubmitted(id: string) {
  const blockId = pending.value
  pending.value = null
  if (!blockId) return
  submitted.value = { ...submitted.value, [blockId]: id }
}
</script>

<template>
  <template v-for="proposal in visibleCards()" :key="proposal.block_id">
    <v-card v-if="submitted[proposal.block_id]" variant="outlined" class="fb-agent-card mt-2">
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon color="success" size="19">mdi-check-circle-outline</v-icon>
          <span class="t-title">已提交</span>
        </div>
        <div class="t-body mb-3">反馈中心里能看到它的进展。</div>
        <v-btn
          variant="outlined"
          color="secondary"
          size="small"
          @click="router.push(`/feedback/${submitted[proposal.block_id]}`)"
        >
          查看这条反馈
        </v-btn>
      </div>
    </v-card>

    <v-card v-else variant="outlined" class="fb-agent-card mt-2">
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon size="19">mdi-robot-outline</v-icon>
          <span class="t-title">我确认这里更像是平台问题，而不是你的使用方式</span>
        </div>
        <div class="t-meta mb-2">
          {{ KIND_LABEL[proposal.payload.kind] }} · {{ proposal.author_handle }} ·
          {{ relTime(proposal.authored_at) }}
        </div>

        <div class="t-title mb-2">{{ proposal.payload.title }}</div>

        <!-- 用户原话。**放在判断依据上面**，理由见文件开头：这是这张卡唯一一个
             不用解释就成立的诚信机制，读的人该先看见它。 -->
        <div class="fb-agent-card__said mb-3">
          <div class="t-eyebrow mb-1">你当时说的</div>
          <div class="t-body fb-agent-card__quote">{{ proposal.payload.user_said }}</div>
        </div>

        <div v-if="proposal.payload.why" class="fb-agent-card__reason mb-3">
          <div class="t-eyebrow mb-1">判断依据（为什么这不是你的使用方式）</div>
          <div class="t-body fb-agent-card__text">{{ proposal.payload.why }}</div>
        </div>

        <!-- 展开区：三段现场。默认收起，见上面那段注释。
             高度用 v-expand-transition 过渡（仓库里另外三处也是这么做的）：直接跳
             出来会让人以为自己点错了 —— 卡片底下凭空多出三行，而按钮上的字同时从
             「查看详情」变成「收起详情」。它走的是 Vuetify 自己的那组 class，所以
             style.css 里那条 prefers-reduced-motion 兜底照样管得住它。 -->
        <v-expand-transition>
          <div v-if="expanded.has(proposal.block_id)" class="fb-agent-card__evidence mb-3">
            <div v-if="proposal.payload.what_happened" class="fb-evidence-block">
              <div class="t-eyebrow mb-1">发生了什么</div>
              <div class="t-body fb-agent-card__text">{{ proposal.payload.what_happened }}</div>
            </div>
            <div v-if="proposal.payload.repro" class="fb-evidence-block">
              <div class="t-eyebrow mb-1">复现步骤</div>
              <pre class="fb-evidence-pre">{{ proposal.payload.repro }}</pre>
            </div>
            <div v-if="proposal.payload.evidence" class="fb-evidence-block">
              <div class="t-eyebrow mb-1">证据</div>
              <div class="t-body fb-agent-card__text">{{ proposal.payload.evidence }}</div>
            </div>
            <!-- 日志只给个长度，不铺开：它是最大的一段（上限两万字），而这一屏的
                 目的是让人决定要不要提交，不是读日志。真正的日志随反馈一起走。 -->
            <div v-if="proposal.payload.logs" class="fb-evidence-block">
              <div class="t-eyebrow mb-1">日志</div>
              <div class="t-meta">已附上（{{ proposal.payload.logs.length }} 字），跟着反馈一起提交</div>
            </div>
            <div v-if="proposal.payload.session_id || proposal.payload.environment" class="t-meta">
              <template v-if="proposal.payload.session_id">会话 {{ proposal.payload.session_id }}</template>
              <template v-if="proposal.payload.session_id && proposal.payload.environment"> · </template>
              <template v-if="proposal.payload.environment">{{ proposal.payload.environment }}</template>
            </div>
          </div>
        </v-expand-transition>

        <div class="d-flex align-center flex-wrap ga-2">
          <v-btn
            variant="text"
            color="secondary"
            size="small"
            :prepend-icon="expanded.has(proposal.block_id) ? 'mdi-chevron-up' : 'mdi-chevron-down'"
            @click="toggleExpanded(proposal.block_id)"
          >
            {{ expanded.has(proposal.block_id) ? '收起详情' : '查看详情' }}
          </v-btn>
          <v-btn variant="text" color="secondary" size="small" @click="dismiss(proposal)">不用</v-btn>
          <v-spacer />
          <v-btn color="primary" size="small" @click="openDrawer(proposal)">提交反馈</v-btn>
        </div>
      </div>
    </v-card>
  </template>

  <!-- 抽屉挂在卡片外面：它 temporary、fixed 定位，跟着卡片一起被条件渲染的话，
       点「提交反馈」到抽屉出现之间会多一帧空档，看起来像没反应。 -->
  <SubmitFeedbackDrawer @submitted="onSubmitted" />
</template>

<style scoped>
/* 圆角不在这里写：VCard 默认的 rounded="xl"(24px) 带 !important，scoped 的 12px
   压不过它（FeedbackCard.vue 里有同一段说明）。 */
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
