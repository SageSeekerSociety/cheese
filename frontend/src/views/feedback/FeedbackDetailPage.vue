<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, getFeedback } from '@/api'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import FeedbackCommentsThread from '@/components/feedback/FeedbackCommentsThread.vue'
import FeedbackStatusChip from '@/components/feedback/FeedbackStatusChip.vue'
import FeedbackStatusTimeline from '@/components/feedback/FeedbackStatusTimeline.vue'
import { t } from '@/i18n'
import { isClosed, KIND_LABEL, SOURCE_LABEL } from '@/lib/feedbackMeta'
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

const id = computed(() => String(route.params.id))
/** 只在这条详情确实是当前这条时才画它。慢响应后到时页面已经换了条目的情况见
 *  `store.loadDetail` 里那个 `detailId` 比较。 */
const item = computed(() => (store.detail?.id === id.value ? store.detail : null))

const isPrivate = computed(() => item.value?.visibility === 'private')
/** 不能公开的条目（私密 / 安全问题）：没有支持按钮、没有分享。 */
const restricted = computed(() => !!item.value && (isPrivate.value || item.value.security))
const supportable = computed(() => !!item.value && !isClosed(item.value.status))

const commentDraft = ref('')
const posting = ref(false)
const showCopied = ref(false)

/** 底部的评论框收起时只有一行，点开才变成多行框。收起不是图省事：这个框是
 *  `position: sticky` 挂在评论区底部的（理由写在 `.fb-composer` 那条注释里），
 *  一直占着屏幕底下一条，常驻三行加按钮差不多 130px 就是永久少掉的一屏。 */
const composerOpen = ref(false)
const composerToggle = ref<HTMLButtonElement | null>(null)
const composerInput = ref<{ focus: () => void } | null>(null)

/** 「这条不该被看见」。服务端把三种情况合成同一个 404（见页面开头那段），store 又
 *  把 404 和「网络抖了一下」合成同一句 `store.error`，所以失败之后要再问一次服务端，
 *  只为把这两件事分开 —— 它们画的话不一样（§9.5）：一种是「这条反馈不存在」，另一种
 *  是「详情加载失败」。对一条已经删掉的反馈说「检查网络后重试」，是让人拴在一条永远
 *  拉不回来的记录上反复重试。
 *
 *  **只在失败这条路上多发一次请求**：正常路径一次都不多发（详情那一刻已经拿到了）。
 *  403 和 404 都算「看不见」—— 别人的私密反馈服务端回的是 403。 */
const gone = ref(false)

async function reload() {
  gone.value = false
  await store.loadDetail(id.value)
  if (item.value || !store.error) return
  try {
    await getFeedback(id.value)
  } catch (error) {
    if (error instanceof ApiError && (error.status === 403 || error.status === 404)) gone.value = true
  }
}

/** 拉不到时那一块的两句话（§9.5）。判据只有「服务端认不认这条」一个。 */
const missingState = computed(() =>
  gone.value
    ? {
        icon: 'mdi-lock-outline',
        title: t('feedback.detail.missing.title'),
        desc: t('feedback.detail.missing.desc'),
      }
    : {
        icon: 'mdi-alert-circle-outline',
        title: t('feedback.detail.error.title'),
        desc: t('feedback.detail.error.desc'),
      }
)

onMounted(() => void reload())
// 从「相关反馈」跳到另一条时组件不会重建（同一个路由，只换参数），所以要自己跟。
watch(id, () => void reload())

async function submitComment(body: string, parentId?: string): Promise<boolean> {
  if (!item.value) return false
  return await store.addComment(item.value.id, body, parentId)
}

/** 楼内的一条回复。结果用回调带回给评论条（`emit` 不能 await，所以结果是这么过去的）：
 *  发失败了框和草稿都留着，人按一下就能重试。 */
function submitReply(parentId: string, body: string, done: (ok: boolean) => void) {
  void submitComment(body, parentId).then(done)
}

/** 底部那个输入框发成功了才清空 —— 发失败还清掉，等于把刚写的两段话丢掉。
 *  清空是调用方的事（`submitComment` 只管一条评论的正文）。
 *
 *  `posting` 是这一页自己的重入闸：store 的 `addComment` 没有（也不该有）——
 *  它按「一次调用一条评论」办事，而这里连按两下会发两条一模一样的出去。 */
async function postComment() {
  if (posting.value) return
  posting.value = true
  try {
    if (await submitComment(commentDraft.value)) {
      commentDraft.value = ''
      // 发完把框收回去。焦点这一刻在按钮上，而那个按钮马上要跟着一起卸载 ——
      // 交给收起态那条，键盘用户不会掉到 body 上（下一个 Tab 从页头重来）。
      composerOpen.value = false
      await nextTick()
      composerToggle.value?.focus()
    }
  } finally {
    posting.value = false
  }
}

/** 点开收起态那一条。展开之后要把光标送进去，不然人还要再点一下那个框 ——
 *  「点一下就能打字」是这一条唯一的卖点。 */
async function openComposer() {
  composerOpen.value = true
  await nextTick()
  composerInput.value?.focus()
}

/** 空草稿失焦就收回去：收起来的价值就是别占地方，留一个空框在那儿只是白占。
 *  草稿非空绝不收 —— 那是把人写下的字藏起来。点「发表评论」也会走到这里，
 *  但那时草稿非空，收不回去。 */
function onComposerBlur() {
  if (!commentDraft.value.trim()) composerOpen.value = false
}

/** 点赞 / 删除一条评论。走 store，和这一页其余部分一样；评论条只 emit，不发请求。
 *  这里三个包装只做一件事：把「当前这条反馈的 id」补上（详情可能在请求在飞的
 *  时候被换掉，store 自己会挡住那种情况，见 `_detailIfCurrent`）。 */
function toggleCommentLike(commentId: string) {
  if (!item.value) return
  void store.toggleCommentLike(item.value.id, commentId)
}

function removeComment(commentId: string) {
  if (!item.value) return
  void store.deleteComment(item.value.id, commentId)
}

function loadMoreComments() {
  if (!item.value) return
  void store.loadMoreComments(item.value.id)
}

function loadMoreReplies(parentId: string) {
  if (!item.value) return
  void store.loadMoreReplies(item.value.id, parentId)
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
        <v-icon size="28" class="fb-state__icon">{{ missingState.icon }}</v-icon>
        <div class="fb-state__title">{{ missingState.title }}</div>
        <p class="fb-state__desc">{{ missingState.desc }}</p>
        <!-- 服务端那句话照直画出来，但「这条不存在」那一态不画：那句话说的是「这一次
             为什么没拉到」，而在 404 这一态它只会把上面那句换个说法再说一遍。 -->
        <p v-if="!gone" class="fb-state__raw t-meta-read">{{ store.error }}</p>
        <v-btn variant="text" color="secondary" size="small" class="fb-state__action" @click="router.push('/feedback')">
          回到反馈中心
        </v-btn>
      </div>
    </div>

    <div v-else class="fb-page__inner page-container--wide">
      <button class="fb-back" @click="router.push('/feedback')">
        <v-icon size="15">mdi-chevron-left</v-icon>反馈中心
      </button>

      <!-- 断点走 CSS 媒体查询（≥1280 双栏），不再经 `useDisplay()`：同一档宽度在
           JS 和 CSS 里各写一遍，两边迟早会分家，而这一页的版式本来就全靠 CSS。 -->
      <div class="fb-layout">
        <main class="fb-main">
          <h1 class="t-page-title fb-title">{{ item.title }}</h1>

          <div class="d-flex align-center flex-wrap ga-2 mb-2">
            <FeedbackStatusChip :status="item.status" />
            <span class="chip-neutral">{{ KIND_LABEL[item.kind] }}</span>
            <!-- 私密在详情页比在列表里更要说清楚：读的人可能正是从别处点进来的，
                 他需要一眼知道这条没有公开。中性色，和卡片上同一个呈现。 -->
            <span
              v-if="isPrivate"
              class="chip-neutral"
              title="私密反馈：只有你、平台管理员、以及提出它时在那个房间里的人能看到，其他人看不到它"
            >
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
               才是「什么时候提的」。挤在一行会让编号看着像作者名的一部分。
               头像是这一页最大的一处（28px）：详情页是唯一一处读者真的会停下来看
               「这是谁提的」的地方，列表里那个 18px 的在这里就太小了。 -->
          <div class="t-meta mb-1 d-flex align-center ga-2">
            <FeedbackAuthorAvatar
              :handle="item.author_handle"
              :is-agent="item.author_is_agent"
              :avatar-id="item.author_avatar_id"
              :size="28"
            />
            <span>{{ item.display_id }} · {{ item.author_handle }} · {{ relTime(item.created_at) }}</span>
          </div>
          <!-- 提案卡发出来的那条有两个名字：agent 找出来的、人发出去的。两个都写，
               因为「这是谁提的」在这条路径上有两个都对但不同的答案。 -->
          <div v-if="item.submitted_by_handle" class="t-meta mb-4">由 {{ item.submitted_by_handle }} 提交</div>
          <div v-else class="mb-4" />

          <section v-if="item.problem" class="fb-section">
            <div class="t-eyebrow mb-1">问题描述</div>
            <p class="t-reading fb-text">{{ item.problem }}</p>
          </section>

          <section v-if="item.why" class="fb-section">
            <div class="t-eyebrow mb-1">为什么需要</div>
            <p class="t-reading fb-text">{{ item.why }}</p>
          </section>

          <section v-if="item.expectation" class="fb-section">
            <div class="t-eyebrow mb-1">期望方案</div>
            <p class="t-reading fb-text">{{ item.expectation }}</p>
          </section>

          <!-- Agent 发现的那一类：现场三段。人提交的反馈没有这三段，整块不出现。
               三段的小标题写中文，和界面其余部分一致：「REPRO」对第一次看的人来说
               不是一个词（docs/design-system.md §8.0）。 -->
          <section v-if="item.what_happened || item.repro || item.evidence" class="fb-section">
            <div class="t-eyebrow mb-2">现场</div>
            <div class="fb-evidence">
              <div v-if="item.what_happened" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">发生了什么</div>
                <p class="t-reading fb-text">{{ item.what_happened }}</p>
              </div>
              <div v-if="item.repro" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">复现步骤</div>
                <pre class="fb-pre">{{ item.repro }}</pre>
              </div>
              <div v-if="item.evidence" class="fb-evidence__block">
                <div class="t-eyebrow mb-1">证据</div>
                <p class="t-reading fb-text">{{ item.evidence }}</p>
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

            <!-- 所有动作都走 store，和页面其余部分一样（评论条自己不发请求）。
                 点赞不刷新任何 Tab 的计数，删除会同时把楼里的回复从本地摘掉 ——
                 两件事的理由都写在 store 里那两条 action 上。

                 `has-more` 问的是**服务端**那一页取完没有：本地还剩几条说明不了
                 「后面还有没有」，两者是不同的东西。两个 `loading-*` 是这一页
                 唯一的重入闸，理由写在 store 上那两条 action 里。 -->
            <FeedbackCommentsThread
              :comments="item.thread"
              :has-more="!!item.thread_next_cursor"
              :loading-more="store.moreCommentsLoading"
              :loading-replies="store.moreRepliesLoading"
              @reply="submitReply"
              @like="toggleCommentLike"
              @remove="removeComment"
              @load-more="loadMoreComments"
              @load-replies="loadMoreReplies"
            />

            <!-- 操作栏在的时候，评论框抬到它上面（见 `.fb-composer--raised`）。 -->
            <div class="fb-composer" :class="{ 'fb-composer--raised': !restricted }">
              <!-- 收起态是一条**真按钮**，不是一个带 @click 的 div：Tab 停得下、
                  回车开得了、读屏念得出名字。这一批刚在卡片上付过这个学费
                  （见 FeedbackCard.vue）。看着像输入框（连光标都是 text），
                  但它此刻的职责是「点一下打开」。 -->
              <button
                v-if="!composerOpen"
                ref="composerToggle"
                type="button"
                class="fb-composer__open"
                @click="openComposer"
              >
                写下你的评论…
              </button>
              <template v-else>
                <!-- max-rows：autoGrow 是全局默认，而这个框挂在视口底下 ——
                     不封顶的话一段长评论会把整屏顶掉。 -->
                <v-textarea
                  ref="composerInput"
                  v-model="commentDraft"
                  autocomplete="off"
                  placeholder="补充你遇到的情况，或者说明为什么这个改动对你重要"
                  rows="3"
                  max-rows="8"
                  hide-details
                  @blur="onComposerBlur"
                />
                <div class="d-flex justify-end mt-2">
                  <!-- 这一页唯一的琥珀在底部那条操作栏里（§7.4：详情页那一颗在操作栏）。
                       这一颗跟着降成中性 tonal —— 两颗琥珀同屏时，读的人分不出哪一颗是
                       「这一页的主操作」，而「只有一颗」正是琥珀全部的意思。 -->
                  <v-btn
                    color="secondary"
                    variant="tonal"
                    size="small"
                    :disabled="!commentDraft.trim()"
                    :loading="posting"
                    @click="postComment"
                  >
                    发表评论
                  </v-btn>
                </div>
              </template>
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

      <!-- 服务端的原话（412 的「已经办完了，不再接受支持」也走这里）。
           可关：这一页上的失败大多是可重试的一次性失败（评论没发出去、下一页没
           取到），一句话挂在页面底部陪着你看完剩下三条评论，读的人只会以为页面
           坏了。关掉它不影响任何状态 —— 没成的操作本来就没改任何东西。 -->
      <v-alert
        v-if="store.error"
        type="error"
        density="compact"
        variant="tonal"
        closable
        class="mt-4"
        @click:close="store.clearError()"
      >
        {{ store.error }}
      </v-alert>

      <!-- 64px 粘底操作栏（§4.4）。用户侧这一栏里的主操作是**支持** —— 这一页唯一的
           琥珀（§7.4）。它原先是漂在正文中间的一颗按钮，滚过两屏就够不着了，而它是
           这一页唯一一件「读完之后能做的事」；现在它钉在屏幕底下，读到哪里都在。
           分享是次要动作，中性描边。
           「已支持」那一态跟着退回中性 tonal：琥珀画的是「现在该做这件」，而它已经
           做完了。三个不依赖颜色的信号还在（文字、实心图标、计数变 --ink）。
           私密和安全问题整条栏都不画：那两类连支持都不成立（支持是公开表态），
           分享出去的链接对别人也打不开 —— 摆两颗按不动的按钮比不摆更坏。 -->
      <div v-if="!restricted" class="fb-actionbar">
        <v-btn
          :color="item.supported ? 'secondary' : 'primary'"
          :variant="item.supported ? 'tonal' : undefined"
          :prepend-icon="item.supported ? 'mdi-thumb-up' : 'mdi-thumb-up-outline'"
          :disabled="!supportable"
          :title="supportable ? '' : '已办完，无需再支持'"
          @click="store.toggleSupport(item.id)"
        >
          {{ item.supported ? '已支持' : '支持这个反馈' }}
          <span class="fb-support-count">{{ item.supports }}</span>
        </v-btn>
        <v-btn variant="outlined" color="secondary" prepend-icon="mdi-share-variant-outline" @click="share">
          分享
        </v-btn>
      </div>
    </div>

    <v-snackbar v-model="showCopied" :timeout="2500">链接已复制</v-snackbar>
  </div>
</template>

<style scoped>
.fb-page {
  /* 底内边距是 0，**别再往回加**：这一页最后两样东西都黏在底边上（评论框、操作栏），
     而 `sticky` 量的是滚动容器**内容盒**的下沿，不是它的边框盒 —— 多出来的
     底内边距会变成它们下面的一条带子，评论从那里往上滚、从框底下露出来（实测
     48px 的底内边距就是 48px 高的一条，半行评论卡在输入框下面）。评论框那一段
     的留白由它自己的下内边距给（见 `.fb-composer`），所以「黏住时」和「滚到底时」
     长得一模一样，不会到最后突然往下挪一截。
     列表那几页没有黏住的东西，它们的 48px 照旧。 */
  padding: 16px 16px 0;
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
/* 拉不到时那一块（§9.2 的内容块）：宽 320、水平居中，主文案 15/--lh-15/600/--ink，
   副文案 13/--lh-13/--muted，主副之间 8px。整块不用 --faint：这两句话是要人读的
   （AA 4.5:1），而 --faint 在浅色主题下四种底色上都到不了 3:1。 */
.fb-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 320px;
  margin: 0 auto;
  padding: 64px 0;
  gap: 8px;
}
.fb-state__icon {
  color: var(--muted);
}
.fb-state__title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  text-align: center;
}
.fb-state__desc {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-align: center;
}
.fb-state__raw {
  margin: 0;
  text-align: center;
  word-break: break-word;
}
.fb-state__action {
  margin-top: 4px;
}
/* 一栏是默认，两栏是 ≥1280 那一档（§4.4 的第一个断点）。 */
.fb-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: start;
  gap: 20px;
}
/* 正文栏锁在阅读宽度上（§14 第 24 条：660 ± 1px）。一栏这一档真正管的是 768–1279
   那段平板宽度 —— 整栏铺满时一行排到 60 个汉字以上，回行就找不到下一行的开头了。
   右栏一起锁是为了两块共用同一条左沿，不然右栏会比正文宽出去一截。
   手机上本来就没有 660 宽，这条是空操作。 */
.fb-main,
.fb-aside {
  max-width: var(--page-w-read);
  justify-self: center;
}
@media (min-width: 1280px) {
  .fb-layout {
    grid-template-columns: minmax(0, var(--page-w-read)) 280px;
    justify-content: space-between;
    gap: 32px;
  }
  /* 两栏这一档，轨道本身已经是那两个宽度（正文正好 --page-w-read），上面那条上限
     留给一栏那一档就好 —— 不留神会把右栏也压成 660 的宽度。 */
  .fb-main,
  .fb-aside {
    max-width: none;
  }
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
  line-height: var(--lh-12);
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
/* 评论框黏在**评论区自己**身上，不黏在视口上。
 *
 * 要解决的是「评论一多，想评论就得滚到最底下」。直接 `position: fixed` 到视口底部
 * 也能解决，但代价是它从打开这一页的第一秒就在 —— 而这一页上面还有正文和「现场」
 * 那三段，读的人多数是来读它们的，一个常驻条会一直压着那一屏、盖住最后一段。
 * 挂在评论区这个 section 上，`sticky` 的行为正好是要的那一种：
 *   正文还在屏幕上（评论区还在屏幕外）→ 它不出现；
 *   滚进评论 → 贴在视口底部，滚到哪儿都够得着；
 *   滚到评论区末尾 → 落回自己原来的位置，不会「都到底了还浮着一条」。
 * 另外 `sticky` 只占左边这一列 —— `fixed` 会横跨整页，把右栏「进展」一起盖住。
 *
 * **不画上边线，只留不透明的底色**：左栏（正文那一条）到此为止，右栏「进展」在那
 * 个高度上是空的，一条只画到一半的横线看着像坏了。分块由框自己那圈描边负责 ——
 * 和 ChatPanel 里那个输入区同一个办法。
 *
 * `.fb-page` 才是这一页的滚动容器（页根自己领滚动，见 scroll.spec.ts），
 * 所以 `bottom: 0` 贴的是它内容盒的下沿。
 *
 * **窄屏底下那条一级导航不用管**：`v-bottom-navigation` 会把自己注册成一个底部
 * 布局项，`v-main` 因此拿到 `padding-bottom`，滚动容器本来就在它上面 —— 再自己
 * 让开 56px 反而会在框底下留出一条能把评论露出来的带子。 */
.fb-composer {
  position: sticky;
  bottom: 0;
  z-index: 2;
  margin-top: 12px;
  /* 下内边距就是这一页末尾的留白（`.fb-page` 那 48px 挪到这儿了）。 */
  padding: 8px 0 16px;
  background: var(--canvas);
}
/* 底下那条操作栏在的时候，评论框抬到它上面一栏高（64px，和 `.fb-actionbar` 的
   height 是同一个数，改一处必须改两处）。两条都黏在底边的话会叠在一起 ——
   评论框属于评论区，操作栏属于整页，上下有先后。 */
.fb-composer--raised {
  bottom: 64px;
}
/* 收起态。圆角和描边跟展开后的 v-textarea 同一套（那件组件的全局默认就是
   outlined + rounded lg），所以点开那一下框不会「换一件衣服」。 */
.fb-composer__open {
  display: block;
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--muted);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: text;
}
.fb-composer__open:hover,
.fb-composer__open:focus-visible {
  border-color: var(--line-2);
  background: var(--fill);
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
/* 64px 粘底操作栏（§4.4）。高度写死 64：它和评论框一起占着屏幕底下两条，再高就
   从正文那里拿走了。粘的是**页面**底边（`.fb-page` 是滚动容器），不是评论区 ——
   它装的是整页的动作，不是评论的动作。
   z-index 比评论框高一层：评论框黏在它上面 64px 处，两层不重叠，但滚动的正文会
   同时从两层底下经过，谁在上面要在源码里看得出来。
   `--surface` + 上边线：这一条浮在画布上，得有一道自己的边界，理由和卡片一样
   （docs/design-system.md §3.4）。 */
.fb-actionbar {
  position: sticky;
  bottom: 0;
  z-index: 3;
  display: flex;
  align-items: center;
  box-sizing: border-box;
  height: 64px;
  padding: 0 16px;
  /* iOS 那条横条压在操作栏上时，底下那颗按钮点不到。安全区只在有它的时候才加，
     没有这个变量时 `0px` 是空操作。 */
  padding-bottom: env(safe-area-inset-bottom, 0);
  gap: 8px;
  background: var(--surface);
  border-top: 1px solid var(--line);
}
</style>
