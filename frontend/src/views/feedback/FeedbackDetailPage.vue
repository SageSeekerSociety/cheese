<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import FeedbackStatusChip from '@/components/feedback/FeedbackStatusChip.vue'
import FeedbackStatusTimeline from '@/components/feedback/FeedbackStatusTimeline.vue'
import { KIND_LABEL, SOURCE_LABEL } from '@/lib/feedbackMock'
import { relTime } from '@/lib/relTime'
import { myHandle } from '@/me'
import { useFeedbackStore } from '@/stores/feedback'

// 公开反馈详情页 (/feedback/:id)。
//
// 左边是**这条反馈本身**（描述、方案、评论），右边是**它现在的处境**（走到哪一步、
// 有多少人在等、还有哪些是同一件事）。这个分工是这块最重要的一个决定：把状态
// Timeline 放进左边正文里，读的人就会先读它——而它回答的是「我该不该继续关注」，
// 不是「这条说的是什么」。
defineOptions({ name: 'FeedbackDetailPage' })

const store = useFeedbackStore()
const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()

const id = computed(() => String(route.params.id))
const item = computed(() => store.byId(id.value))
const related = computed(() => store.related(id.value))

/** 私密反馈在普通用户眼里**不存在**：列表层就滤掉了，直达链接也必须挡住 ——
 *  只靠列表不显示，等于谁把链接发出来谁就能看。真接后端时这一层应该在服务端。 */
const privateForMe = computed(() => !!item.value && item.value.visibility === 'private' && store.role !== 'admin')

const commentDraft = ref('')
const showCopied = ref(false)

function submitComment() {
  if (!item.value) return
  store.addComment(item.value.id, myHandle() || '我', commentDraft.value)
  commentDraft.value = ''
}

async function share() {
  try {
    await navigator.clipboard.writeText(window.location.href)
    showCopied.value = true
  } catch {
    showCopied.value = false
  }
}
</script>

<template>
  <div class="fb-page">
    <div v-if="!item" class="fb-page__inner page-container">
      <div class="fb-state">
        <div class="t-body">没有这条反馈，它可能被删除了。</div>
        <v-btn variant="text" color="secondary" size="small" @click="router.push('/feedback')">回到反馈中心</v-btn>
      </div>
    </div>

    <div v-else-if="privateForMe" class="fb-page__inner page-container">
      <div class="fb-state">
        <v-icon size="28" class="mb-2">mdi-lock-outline</v-icon>
        <div class="t-body mb-1">这条反馈是私密的。</div>
        <div class="t-meta mb-3">只有管理员能看到它的内容。提交者在提交时选择了「私密」。</div>
        <v-btn variant="text" color="secondary" size="small" @click="router.push('/feedback')">回到反馈中心</v-btn>
      </div>
    </div>

    <div v-else class="fb-page__inner page-container--wide">
      <button class="fb-back" @click="router.push('/feedback')">
        <v-icon size="15">mdi-chevron-left</v-icon>反馈中心
      </button>

      <div class="fb-layout" :class="{ 'fb-layout--narrow': !mdAndUp }">
        <main class="fb-main">
          <h1 class="t-page-title fb-title">{{ item.title }}</h1>

          <div class="d-flex align-center flex-wrap ga-2 mb-2">
            <FeedbackStatusChip :status="item.status" />
            <span class="chip-neutral">{{ KIND_LABEL[item.kind] }}</span>
            <span v-if="item.source === 'agent'" class="chip-neutral">
              <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
            </span>
            <span v-for="tag in item.tags" :key="tag" class="chip-neutral">{{ tag }}</span>
          </div>
          <div class="t-meta mb-4">
            <span v-if="store.role === 'admin'">{{ item.id }} · </span>{{ item.author }} · {{ relTime(item.createdAt) }}
          </div>

          <div class="d-flex align-center flex-wrap ga-2 mb-6">
            <v-btn
              :variant="item.supportedByMe ? 'flat' : 'outlined'"
              :color="item.supportedByMe ? 'primary' : 'secondary'"
              prepend-icon="mdi-thumb-up-outline"
              :disabled="item.status === 'resolved'"
              @click="store.toggleSupport(item.id)"
            >
              {{ item.supportedByMe ? '已支持' : '支持这个反馈' }}
              <span class="fb-support-count">{{ item.supports }}</span>
            </v-btn>
            <v-btn variant="outlined" color="secondary" prepend-icon="mdi-share-variant-outline" @click="share"
              >分享</v-btn
            >
          </div>

          <section class="fb-section">
            <div class="t-eyebrow mb-1">问题描述</div>
            <p class="t-body">{{ item.problem }}</p>
          </section>

          <section v-if="item.why" class="fb-section">
            <div class="t-eyebrow mb-1">为什么需要</div>
            <p class="t-body">{{ item.why }}</p>
          </section>

          <section v-if="item.expectation" class="fb-section">
            <div class="t-eyebrow mb-1">期望方案</div>
            <p class="t-body">{{ item.expectation }}</p>
          </section>

          <!-- Agent 发现的那一类：现场三段。人提交的反馈没有这三段，整块不出现。 -->
          <section v-if="item.whatHappened || item.repro || item.evidence" class="fb-section">
            <div class="t-eyebrow mb-2">现场</div>
            <div class="fb-evidence">
              <div v-if="item.whatHappened" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">WHAT HAPPENED</div>
                <p class="t-body">{{ item.whatHappened }}</p>
              </div>
              <div v-if="item.repro" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">REPRO</div>
                <pre class="fb-pre">{{ item.repro }}</pre>
              </div>
              <div v-if="item.evidence" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">EVIDENCE</div>
                <p class="t-body">{{ item.evidence }}</p>
              </div>
            </div>
          </section>

          <section class="fb-section">
            <div class="t-eyebrow mb-3">评论 {{ item.comments.length }}</div>
            <div v-if="item.comments.length" class="fb-comments">
              <div v-for="c in item.comments" :key="c.id" class="fb-comment">
                <div class="d-flex align-center ga-2 mb-1">
                  <span class="fb-comment__author">{{ c.author }}</span>
                  <span v-if="c.byAgent" class="chip-neutral">AI 队友</span>
                  <span class="t-meta">{{ relTime(c.createdAt) }}</span>
                </div>
                <p class="t-body fb-comment__body">{{ c.body }}</p>
              </div>
            </div>
            <div v-else class="t-body c-faint mb-3">还没有人评论。</div>

            <div class="fb-comment-form">
              <v-textarea
                v-model="commentDraft"
                autocomplete="off"
                placeholder="补充你遇到的情况，或者说明为什么这个改动对你重要"
                rows="3"
                hide-details
              />
              <div class="d-flex justify-end mt-2">
                <v-btn :disabled="!commentDraft.trim()" size="small" @click="submitComment">发表评论</v-btn>
              </div>
            </div>
          </section>
        </main>

        <aside class="fb-aside">
          <div class="fb-aside__card">
            <div class="t-eyebrow mb-3">进展</div>
            <FeedbackStatusTimeline :timeline="item.timeline" :status="item.status" />
          </div>

          <div class="fb-aside__card">
            <div class="fb-aside__stat">
              <span class="t-meta">浏览量</span><span class="fb-aside__num">{{ item.views }}</span>
            </div>
            <div class="fb-aside__stat">
              <span class="t-meta">支持人数</span><span class="fb-aside__num">{{ item.supports }}</span>
            </div>
            <div class="fb-aside__stat">
              <span class="t-meta">评论数</span><span class="fb-aside__num">{{ item.comments.length }}</span>
            </div>
          </div>

          <div v-if="related.length" class="fb-aside__card">
            <div class="t-eyebrow mb-3">相关反馈</div>
            <button v-for="r in related" :key="r.id" class="fb-related" @click="router.push(`/feedback/${r.id}`)">
              <span class="fb-related__title">{{ r.title }}</span>
              <span class="t-meta">{{ r.supports }} 人支持</span>
            </button>
          </div>
        </aside>
      </div>
    </div>

    <v-snackbar v-model="showCopied" :timeout="2500">链接已复制</v-snackbar>
  </div>
</template>

<style scoped>
.fb-page {
  padding: 20px 20px 48px;
}
.fb-page__inner {
  margin: 0 auto;
}
.fb-back {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-bottom: 12px;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
}
.fb-back:hover {
  background: var(--fill);
  color: var(--ink);
}
.fb-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 64px 0;
  color: var(--faint);
}
.fb-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  align-items: start;
  gap: 32px;
}
/* 窄屏：右栏掉到正文下面。时间线在手机上仍然要能看见，所以是挪位置，不是隐藏。 */
.fb-layout--narrow {
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
}
.fb-title {
  margin-bottom: 8px;
}
.fb-support-count {
  margin-left: 8px;
  font-family: var(--font-mono);
}
.fb-section + .fb-section {
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid var(--line);
}
.fb-evidence {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.fb-evidence__block + .fb-evidence__block {
  margin-top: 12px;
}
.fb-pre {
  margin: 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
.fb-comments {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-bottom: 20px;
}
.fb-comment__author {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.fb-comment__body {
  margin: 0;
  white-space: pre-wrap;
}
.fb-comment-form {
  padding-top: 4px;
}
.fb-aside {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.fb-aside__card {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.fb-aside__stat {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 3px 0;
}
.fb-aside__num {
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.fb-related {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 7px 8px;
  border-radius: var(--radius-md);
  text-align: left;
  cursor: pointer;
}
.fb-related:hover {
  background: var(--fill);
}
.fb-related__title {
  font-size: 13px;
  line-height: 1.45;
  color: var(--text);
}
</style>
