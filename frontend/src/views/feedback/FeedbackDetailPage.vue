<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import FeedbackCommentsFlat from '@/components/feedback/FeedbackCommentsFlat.vue'
import FeedbackCommentsRail from '@/components/feedback/FeedbackCommentsRail.vue'
import FeedbackCommentsThread from '@/components/feedback/FeedbackCommentsThread.vue'
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

/** 这条是不是我自己提的。 */
const mine = computed(() => !!item.value && store.isMine(item.value))
const isPrivate = computed(() => item.value?.visibility === 'private')

/** **别人**的私密反馈在普通用户眼里不存在：列表层就滤掉了，直达链接也必须挡住 ——
 *  只靠列表不显示，等于谁把链接发出来谁就能看。真接后端时这一层应该在服务端。
 *
 *  判据是「不是我的」，不是「是私密的」：自己提的那条私密反馈在这个页面上和普通
 *  反馈完全一样（一样能补充说明、能看进展），只是不参与公开的支持与分享。 */
const hiddenPrivate = computed(() => isPrivate.value && !mine.value && store.role !== 'admin')

const commentDraft = ref('')
const showCopied = ref(false)

/**
 * 评论布局：**原型专用开关**，三种摆法都做出来给人挑，不是已经定了哪一种。
 *
 *   甲 平铺      —— 今天的形状，按时间一条一条（FeedbackCommentsFlat）
 *   乙 一条流    —— 状态推进和评论混在一条竖线上（FeedbackCommentsRail）
 *   丙 两层折叠  —— 回复缩进挂在顶层评论下（FeedbackCommentsThread）
 *
 * 定下来之后这个开关和另外两版一起删掉，只留选中的那个。
 */
type CommentLayout = 'flat' | 'rail' | 'thread'
const commentLayout = ref<CommentLayout>('flat')
const LAYOUTS: { value: CommentLayout; label: string }[] = [
  { value: 'flat', label: '甲·平铺' },
  { value: 'rail', label: '乙·一条流' },
  { value: 'thread', label: '丙·两层折叠' },
]

// 评论是这一页唯一异步的部分，所以骨架只出现在评论区。**这是原型的取巧**：
// 真接口多半一次把整条反馈和它的评论一起返回，那时整页都该是骨架 —— 那需要给
// 「反馈详情」本身再做一个形态（现在没有）。
onMounted(() => {
  void store.load()
})

function submitComment(body: string, parentId?: string) {
  if (!item.value) return
  store.addComment(item.value.id, myHandle() || '我', body, parentId)
}

/** 底部那个输入框用完要清空，清空是调用方的事（submitComment 只管一条评论的正文）。 */
function postComment() {
  submitComment(commentDraft.value)
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
        <div class="t-body">暂无这条反馈</div>
        <div class="t-meta mb-3">它可能已被删除</div>
        <v-btn variant="text" color="secondary" size="small" @click="router.push('/feedback')">回到反馈中心</v-btn>
      </div>
    </div>

    <div v-else-if="hiddenPrivate" class="fb-page__inner page-container">
      <div class="fb-state">
        <v-icon size="28" class="mb-2">mdi-lock-outline</v-icon>
        <div class="t-body mb-1">这条反馈是私密的</div>
        <div class="t-meta mb-3">只有提交它的人和管理员能看到它的内容</div>
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
            <span v-if="item.source === 'agent'" class="chip-neutral">
              <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
            </span>
            <span v-for="tag in item.tags" :key="tag" class="chip-neutral">{{ tag }}</span>
          </div>
          <div class="t-meta mb-4">
            <span v-if="store.role === 'admin'">{{ item.id }} · </span>{{ item.author }} · {{ relTime(item.createdAt) }}
          </div>

          <!-- 「已支持」是中性色（tonal），不是琥珀：琥珀在这一页属于唯一的那个主操作
               ——发表评论（见页面底部）。状态本身还有文字、图标实心、计数变 --ink
               三个不依赖颜色的信号。见 docs/design-system.md §0。 -->
          <!-- 私密反馈两颗按钮都不给：
               支持是公开表态（它决定「热门」怎么排、管理员先看哪条）；
               分享出去的链接对别人根本打不开，那是个死路 —— 给了反而是骗人。 -->
          <div v-if="!isPrivate" class="d-flex align-center flex-wrap ga-2 mb-6">
            <v-btn
              :variant="item.supportedByMe ? 'tonal' : 'outlined'"
              color="secondary"
              :prepend-icon="item.supportedByMe ? 'mdi-thumb-up' : 'mdi-thumb-up-outline'"
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

          <!-- Agent 发现的那一类：现场三段。人提交的反馈没有这三段，整块不出现。
               三段的小标题写中文，和界面其余部分一致：「REPRO」对第一次看的人来说
               不是一个词（docs/design-system.md §8.0）。 -->
          <section v-if="item.whatHappened || item.repro || item.evidence" class="fb-section">
            <div class="t-eyebrow mb-2">现场</div>
            <div class="fb-evidence">
              <div v-if="item.whatHappened" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">发生了什么</div>
                <p class="t-body">{{ item.whatHappened }}</p>
              </div>
              <div v-if="item.repro" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">复现步骤</div>
                <pre class="fb-pre">{{ item.repro }}</pre>
              </div>
              <div v-if="item.evidence" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">证据</div>
                <p class="t-body">{{ item.evidence }}</p>
              </div>
            </div>
          </section>

          <section class="fb-section">
            <div class="fb-comments-head">
              <div class="t-eyebrow">评论 {{ item.comments.length }}</div>
              <!-- 原型开关，和中心页那个「原型身份」一样是给人看不同摆法的，不是
                   功能（见上面 commentLayout 的注释）。用中性色：这一页的琥珀属于
                   「发表评论」。 -->
              <div class="fb-comments-switch">
                <span class="t-eyebrow">原型：评论布局</span>
                <v-btn-toggle v-model="commentLayout" mandatory density="compact" variant="outlined" divided>
                  <v-btn v-for="l in LAYOUTS" :key="l.value" :value="l.value" size="x-small">
                    {{ l.label }}
                  </v-btn>
                </v-btn-toggle>
              </div>
            </div>

            <LoadingSkeleton v-if="store.loading" variant="comment" :rows="2" />

            <template v-else>
              <FeedbackCommentsFlat v-if="commentLayout === 'flat'" :comments="item.comments" />
              <FeedbackCommentsRail
                v-else-if="commentLayout === 'rail'"
                :comments="item.comments"
                :timeline="item.timeline"
              />
              <FeedbackCommentsThread
                v-else
                :comments="item.comments"
                @reply="(parentId: string, body: string) => submitComment(body, parentId)"
              />
            </template>

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
          <!-- 选「乙·一条流」时这张卡不画：同一份 timeline 已经在正文的竖线里了，
               两处都显示迟早会有一处忘了跟着改（这就是乙那一版的取舍，
               见 FeedbackCommentsRail.vue）。 -->
          <div v-if="commentLayout !== 'rail'" class="fb-aside__card">
            <div class="t-eyebrow mb-3">进展</div>
            <FeedbackStatusTimeline :timeline="item.timeline" :status="item.status" />
          </div>

          <div class="fb-aside__card">
            <div class="fb-aside__stat">
              <span class="t-meta">浏览量</span><span class="fb-aside__num">{{ item.views }}</span>
            </div>
            <!-- 私密反馈没有「支持人数」这一格：它恒为 0，摆在那里只会让人以为
                 「还没人支持」，而不是「这件事对私密反馈不成立」。 -->
            <div v-if="!isPrivate" class="fb-aside__stat">
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
/* 「评论 N」和那个原型开关同一行：开关是临时的，不该占一行正文的高度。 */
.fb-comments-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}
.fb-comments-switch {
  display: flex;
  align-items: center;
  gap: 8px;
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
.fb-related {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 8px;
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
