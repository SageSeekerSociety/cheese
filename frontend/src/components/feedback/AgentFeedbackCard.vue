<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import SubmitFeedbackDrawer from './SubmitFeedbackDrawer.vue'

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
// 本轮没有后端，所以这张卡的**数据**是本地写死的（finding 的默认值），而它的
// **行为**是真的：提交走的是和反馈中心同一个 store action，提交完这里会变成一条
// 已提交凭证并链到那条反馈。接真接口时要改的只有 finding 从哪来。
export interface AgentFinding {
  /** 一句话摘要，卡片正文。 */
  summary: string
  /** 简短判断依据：为什么这不是使用方式的问题。 */
  reason: string
  whatHappened: string
  repro: string
  evidence: string
  sessionId?: string
  environment?: string
}

const props = withDefaults(defineProps<{ finding?: AgentFinding }>(), {
  finding: () => ({
    summary: '连续两次上传同名附件时，第二次会静默失败，界面上没有任何提示。',
    reason: '接口返回 200，但附件列表长度没有变化——这是平台在处理同名文件时的行为，不是你的操作方式。',
    whatHappened: '你在同一个话题里上传了两次 `error.log`，消息里始终只有第一条附件。',
    repro: '1. 新建话题\n2. 上传 error.log\n3. 再上传一份同名但内容不同的 error.log\n4. 消息里仍然只有第一条附件',
    evidence: 'POST /topics/{id}/attachments 返回 200，响应里 attachments 的长度与上传前一致；后端没有同名冲突的提示。',
    sessionId: 'sess_2f90bd',
    environment: 'Chrome 141 / Linux / PWA 安装版',
  }),
})

const store = useFeedbackStore()
const router = useRouter()

/** ask → 正在问；submitted → 已提交，卡变成凭证；dismissed → 这一轮不再出现。 */
const state = ref<'ask' | 'submitted' | 'dismissed'>('ask')
const expanded = ref(false)
const submittedId = ref<string | null>(null)

const visible = computed(() => state.value !== 'dismissed')

function openDrawer() {
  store.openSubmit({
    kind: 'bug',
    title: props.finding.summary,
    body: props.finding.reason,
    // 「附带会话日志」默认勾上：这张卡存在的理由就是别让现场丢掉。
    attachLogs: true,
    fromAgent: {
      whatHappened: props.finding.whatHappened,
      repro: props.finding.repro,
      evidence: props.finding.evidence,
      sessionId: props.finding.sessionId,
      environment: props.finding.environment,
    },
  })
}

/** 抽屉提交完成 —— 它是全局共享的那一个，所以这里只知道「刚提交了某一条」。
 *  同一个话题里一般只会有一张这样的卡，用它自己的 id 记下来就够了。 */
function onSubmitted(id: string) {
  state.value = 'submitted'
  submittedId.value = id
}
</script>

<template>
  <template v-if="visible">
    <v-card v-if="state === 'submitted'" variant="outlined" class="fb-agent-card mt-2">
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-1">
          <v-icon color="success" size="19">mdi-check-circle-outline</v-icon>
          <span class="t-title">已提交为 {{ submittedId }}</span>
        </div>
        <div class="t-body mb-3">反馈中心里能看到它的进展。</div>
        <v-btn variant="outlined" color="secondary" size="small" @click="router.push(`/feedback/${submittedId}`)">
          查看这条反馈
        </v-btn>
      </div>
    </v-card>

    <v-card v-else variant="outlined" class="fb-agent-card mt-2">
      <div class="pa-3">
        <div class="d-flex align-center ga-2 mb-2">
          <v-icon size="19">mdi-robot-outline</v-icon>
          <span class="t-title">我确认这里更像是平台问题，而不是你的使用方式</span>
        </div>

        <div class="t-body mb-2">{{ finding.summary }}</div>
        <div class="fb-agent-card__reason mb-3">
          <span class="t-eyebrow">判断依据</span>
          <div class="t-body">{{ finding.reason }}</div>
        </div>

        <!-- 展开区：三段现场。默认收起，见上面那段注释。 -->
        <div v-if="expanded" class="fb-agent-card__evidence mb-3">
          <div class="fb-evidence-block">
            <div class="t-eyebrow mb-1">发生了什么</div>
            <div class="t-body">{{ finding.whatHappened }}</div>
          </div>
          <div class="fb-evidence-block">
            <div class="t-eyebrow mb-1">复现步骤</div>
            <pre class="fb-evidence-pre">{{ finding.repro }}</pre>
          </div>
          <div class="fb-evidence-block">
            <div class="t-eyebrow mb-1">证据</div>
            <div class="t-body">{{ finding.evidence }}</div>
          </div>
          <div v-if="finding.sessionId || finding.environment" class="t-meta">
            <template v-if="finding.sessionId">会话 {{ finding.sessionId }}</template>
            <template v-if="finding.sessionId && finding.environment"> · </template>
            <template v-if="finding.environment">{{ finding.environment }}</template>
          </div>
        </div>

        <div class="d-flex align-center flex-wrap ga-2">
          <v-btn
            variant="text"
            color="secondary"
            size="small"
            :prepend-icon="expanded ? 'mdi-chevron-up' : 'mdi-chevron-down'"
            @click="expanded = !expanded"
          >
            {{ expanded ? '收起详情' : '查看详情' }}
          </v-btn>
          <v-btn variant="text" color="secondary" size="small" @click="state = 'dismissed'">不用</v-btn>
          <v-spacer />
          <v-btn color="primary" size="small" @click="openDrawer">提交反馈</v-btn>
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
/* 判断依据用 inset 底色，和上面那句摘要分开：摘要是「发生了什么」，依据是「凭
   什么说这是平台的问题」，两件事不该长得一样。 */
.fb-agent-card__reason {
  padding: 8px 10px;
  border-radius: var(--radius-md);
  background: var(--fill);
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
