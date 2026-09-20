<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import FeedbackCommentsThread from '@/components/feedback/FeedbackCommentsThread.vue'
import FeedbackStatusChip from '@/components/feedback/FeedbackStatusChip.vue'
import FeedbackStatusTimeline from '@/components/feedback/FeedbackStatusTimeline.vue'
import { KIND_LABEL, SOURCE_LABEL } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 公开反馈详情页 (/feedback/:id)。
//
// 左边是**这条反馈本身**（描述、现场、评论），右边是**它现在的处境**（走到哪一步、
// 有多少人在等）。这个分工是这块最重要的一个决定：把状态 Timeline 放进左边正文里，
// 读的人就会先读它 —— 而它回答的是「我该不该继续关注」，不是「这条说的是什么」。
//
// 三种「看不见」由服务端合成**同一个** 404，这里也就只画一个状态：不存在、别人的
// 私密反馈、被标成安全问题的，对不相关的人来说长得一模一样。上一轮原型能分开显示
// （「这条反馈是私密的」），那是客户端手里有全部数据才做得到的 —— 真接上服务端之后
// 那句话本身就是泄露：它确认了这条反馈存在。
defineOptions({ name: 'FeedbackDetailPage' })

const store = useFeedbackStore()
const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()

const id = computed(() => String(route.params.id))
/** 只在这条详情确实是当前这条时才画它。慢响应后到时页面已经换了条目的情况见
 *  `store.loadDetail` 里那个 `detailId` 比较。 */
const item = computed(() => (store.detail?.id === id.value ? store.detail : null))

const isPrivate = computed(() => item.value?.visibility === 'private')
/** 不能公开的条目（私密 / 安全问题）：没有支持按钮、没有分享。 */
const restricted = computed(() => !!item.value && (isPrivate.value || item.value.security))
const supportable = computed(() => !!item.value && item.value.status !== 'resolved')

const commentDraft = ref('')
const showCopied = ref(false)

function reload() {
  void store.loadDetail(id.value)
}

onMounted(reload)
// 从「相关反馈」跳到另一条时组件不会重建（同一个路由，只换参数），所以要自己跟。
watch(id, reload)

async function submitComment(body: string, parentId?: string) {
  if (!item.value) return
  await store.addComment(item.value.id, body, parentId)
}

/** 底部那个输入框用完要清空，清空是调用方的事（submitComment 只管一条评论的正文）。 */
async function postComment() {
  const body = commentDraft.value
  commentDraft.value = ''
  await submitComment(body)
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
  <!-- 滚动归这一页自己领，理由见 FeedbackCenterPage 顶部那段注释。 -->
  <div class="fb-page fill-height overflow-y-auto">
    <!-- 还没问出结果之前也画骨架：先画「暂无这条反馈」再换成内容，等于先说错一句
         话再收回去，而这两帧之间在读的人眼里是有先后的。
         容器宽度也要跟到底下那一版（--wide）：骨架是两栏，内容是一栏的话，两块
         正文在到达那一刻会各挪一次位置。 -->
    <div v-if="store.detailLoading" class="fb-page__inner page-container--wide">
      <LoadingSkeleton variant="detail" :rows="3" />
    </div>

    <div v-else-if="!item" class="fb-page__inner page-container">
      <div class="fb-state">
        <v-icon size="28" class="mb-2">mdi-lock-outline</v-icon>
        <div class="t-body mb-1">这条反馈打不开</div>
        <div class="t-meta mb-3">
          它可能不存在，也可能只有提交它的人和管理员能看到 —— 私密反馈对其他人就是这样，链接也一样打不开
        </div>
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
            <!-- 私密在详情页比在列表里更要说清楚：读的人可能正是从别处点进来的，
                 他需要一眼知道这条没有公开。中性色，和卡片上同一个呈现。 -->
            <span v-if="isPrivate" class="chip-neutral" title="私密反馈：只有你和管理员能看到，其他人看不到它">
              <v-icon size="12">mdi-lock-outline</v-icon>私密
            </span>
            <span v-if="item.security" class="chip-neutral">
              <v-icon size="12">mdi-shield-alert-outline</v-icon>安全
            </span>
            <span v-if="item.author_is_agent" class="chip-neutral">
              <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
            </span>
            <span v-for="tag in item.tags" :key="tag" class="chip-neutral">{{ tag }}</span>
          </div>
          <!-- 编号和作者分两行：`FB-1042` 是给人念、给人粘的，作者名之后那一串
               才是「什么时候提的」。挤在一行会让编号看着像作者名的一部分。 -->
          <div class="t-meta mb-1">
            {{ item.display_id }} · {{ item.author_handle }} · {{ relTime(item.created_at) }}
          </div>
          <!-- 提案卡发出来的那条有两个名字：agent 找出来的、人发出去的。两个都写，
               因为「这是谁提的」在这条路径上有两个都对但不同的答案。 -->
          <div v-if="item.submitted_by_handle" class="t-meta mb-4">由 {{ item.submitted_by_handle }} 提交</div>
          <div v-else class="mb-4" />

          <!-- 「已支持」是中性色（tonal），不是琥珀：琥珀在这一页属于唯一的那个主操作
               ——发表评论（见页面底部）。状态本身还有文字、图标实心、计数变 --ink
               三个不依赖颜色的信号。见 docs/design-system.md §0。 -->
          <!-- 私密和安全问题两颗按钮都不给：
               支持是公开表态（它决定「热门」怎么排、管理员先看哪条）；
               分享出去的链接对别人根本打不开，那是个死路 —— 给了反而是骗人。 -->
          <div v-if="!restricted" class="d-flex align-center flex-wrap ga-2 mb-6">
            <v-btn
              :variant="item.supported ? 'tonal' : 'outlined'"
              color="secondary"
              :prepend-icon="item.supported ? 'mdi-thumb-up' : 'mdi-thumb-up-outline'"
              :disabled="!supportable"
              :title="supportable ? '' : '已解决，无需再支持'"
              @click="store.toggleSupport(item.id)"
            >
              {{ item.supported ? '已支持' : '支持这个反馈' }}
              <span class="fb-support-count">{{ item.supports }}</span>
            </v-btn>
            <v-btn variant="outlined" color="secondary" prepend-icon="mdi-share-variant-outline" @click="share"
              >分享</v-btn
            >
          </div>

          <section v-if="item.problem" class="fb-section">
            <div class="t-eyebrow mb-1">问题描述</div>
            <p class="t-body fb-text">{{ item.problem }}</p>
          </section>

          <section v-if="item.why" class="fb-section">
            <div class="t-eyebrow mb-1">为什么需要</div>
            <p class="t-body fb-text">{{ item.why }}</p>
          </section>

          <section v-if="item.expectation" class="fb-section">
            <div class="t-eyebrow mb-1">期望方案</div>
            <p class="t-body fb-text">{{ item.expectation }}</p>
          </section>

          <!-- Agent 发现的那一类：现场三段。人提交的反馈没有这三段，整块不出现。
               三段的小标题写中文，和界面其余部分一致：「REPRO」对第一次看的人来说
               不是一个词（docs/design-system.md §8.0）。 -->
          <section v-if="item.what_happened || item.repro || item.evidence" class="fb-section">
            <div class="t-eyebrow mb-2">现场</div>
            <div class="fb-evidence">
              <div v-if="item.what_happened" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">发生了什么</div>
                <p class="t-body fb-text">{{ item.what_happened }}</p>
              </div>
              <div v-if="item.repro" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">复现步骤</div>
                <pre class="fb-pre">{{ item.repro }}</pre>
              </div>
              <div v-if="item.evidence" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">证据</div>
                <p class="t-body fb-text">{{ item.evidence }}</p>
              </div>
              <div v-if="item.session_id || item.environment" class="t-meta fb-evidence__block">
                <template v-if="item.session_id">会话 {{ item.session_id }}</template>
                <template v-if="item.session_id && item.environment"> · </template>
                <template v-if="item.environment">{{ item.environment }}</template>
              </div>
            </div>
          </section>

          <section class="fb-section">
            <div class="fb-comments-head">
              <div class="t-eyebrow">评论 {{ item.comments }}</div>
            </div>

            <FeedbackCommentsThread
              :comments="item.thread"
              @reply="(parentId, body) => submitComment(body, parentId)"
            />

            <div class="fb-comment-form">
              <v-textarea
                v-model="commentDraft"
                autocomplete="off"
                placeholder="补充你遇到的情况，或者说明为什么这个改动对你重要"
                rows="3"
                hide-details
              />
              <div class="d-flex justify-end mt-2">
                <v-btn color="primary" :disabled="!commentDraft.trim()" size="small" @click="postComment">
                  发表评论
                </v-btn>
              </div>
            </div>
          </section>
        </main>

        <aside class="fb-aside">
          <div class="fb-aside__card">
            <div class="t-eyebrow mb-3">进展</div>
            <FeedbackStatusTimeline :timeline="item.timeline" :status="item.status" :ladder="store.statusLadder" />
          </div>

          <div class="fb-aside__card">
            <!-- 私密反馈没有「支持人数」这一格：它恒为 0，摆在那里只会让人以为
                 「还没人支持」，而不是「这件事对私密反馈不成立」。 -->
            <div v-if="!restricted" class="fb-aside__stat">
              <span class="t-meta">支持人数</span><span class="fb-aside__num">{{ item.supports }}</span>
            </div>
            <div class="fb-aside__stat">
              <span class="t-meta">评论数</span><span class="fb-aside__num">{{ item.comments }}</span>
            </div>
          </div>

          <!-- 这条反馈是从哪个话题来的。没有话题的那种（harness 在沙箱里撞的墙）
               就没有这一格 —— 那正是它要报的那类问题。 -->
          <div v-if="item.topic_id" class="fb-aside__card">
            <div class="t-eyebrow mb-2">来源</div>
            <div class="t-meta">由某个话题里的对话发现</div>
          </div>
        </aside>
      </div>

      <!-- 服务端的原话（412 的「已解决不再接受支持」也走这里）。 -->
      <v-alert v-if="store.error" type="error" density="compact" variant="tonal" class="mt-4">
        {{ store.error }}
      </v-alert>
    </div>

    <v-snackbar v-model="showCopied" :timeout="2500">链接已复制</v-snackbar>
  </div>
</template>

<style scoped>
.fb-page {
  padding: 16px 16px 48px;
}
.fb-page__inner {
  margin: 0 auto;
}
/* 负的左边距配自己的内边距：hover 时有块可点的底色，但字仍然和下面的标题左对齐
   （不加负边距的话，这行会比整页内容右缩 8px）。 */
.fb-back {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin: 0 0 12px -8px;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
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
/* 用户写的正文是**多段**的（换行要保留），不是一句一句拼接的 —— 不写这个，
   提交时分的段落到详情页会挤成一整段。 */
.fb-text {
  white-space: pre-wrap;
}
.fb-section + .fb-section {
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid var(--line);
}
.fb-evidence {
  padding: 12px 16px;
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
.fb-comments-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.fb-comment-form {
  padding-top: 4px;
}
.fb-aside {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.fb-aside__card {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.fb-aside__stat {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 4px 0;
}
.fb-aside__num {
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
</style>
