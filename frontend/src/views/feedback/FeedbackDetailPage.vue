<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { useUserRefResolver } from '@/composables/useUserRefResolver'

import FeedbackDetailPageView from './FeedbackDetailPageView.vue'

import { ApiError, getFeedback } from '@/api'
import { claimFeedback, releaseFeedback } from '@/api/feedbackClaim'
import FeedbackPageShell from '@/components/feedback/FeedbackPageShell.vue'
import { t } from '@/i18n'
import { closeOverlay } from '@/lib/backOut'
import { useFeedbackStore } from '@/stores/feedback'

// 公开反馈详情页 (/feedback/:id) 的容器：取数、路由、store、领取、评论、状态变更
// 都在这里；画的那一半（以及评论框收没收、删除确认换没换这类纯 UI 状态）在
// `FeedbackDetailPageView.vue`。
//
// 壳（`FeedbackPageShell`）留在这边而不是视图里，是为了让页面源码的第一个模板元素
// 仍然是那个壳 —— `scroll.spec.ts` 钉的就是「反馈页用共用的壳」这件事，看的是**页面**
// 这个文件。壳是 A 级的，配对规则允许容器把它和视图一起渲染。
defineOptions({ name: 'FeedbackDetailPage' })

const store = useFeedbackStore()
const route = useRoute()
const router = useRouter()
// 人名（提交人、评论里回的人、时间线上是谁推的）都走这里：名册与当前项目在容器这一
// 侧算好，视图只拿 `resolveUser` 画。见 `useUserRefResolver`。
const { resolve: resolveUser, navigate } = useUserRefResolver()

const id = computed(() => String(route.params.id))
/** 只在这条详情确实是当前这条时才画它。慢响应后到时页面已经换了条目的情况见
 *  `store.loadDetail` 里那个 `detailId` 比较。 */
const item = computed(() => (store.detail?.id === id.value ? store.detail : null))

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

onMounted(() => void reload())

/** 「返回」回反馈中心：按钮上写的就是这个地名（`feedback.detail.back` /
 *  `feedback.detail.missingBack`），所以去向是定的，不是「往回走一格」—— 从话题里点
 *  开一条反馈（`AgentFeedbackCard`），它又打不开时，`router.back()` 会把人送回那个
 *  话题，而按钮上写的是回中心。身后正是中心就退一格（不在身后再压一条一模一样的），
 *  否则 replace 过去。 */
function backToCenter() {
  closeOverlay(router, '/feedback')
}
// 从「相关反馈」跳到另一条时组件不会重建（同一个路由，只换参数），所以要自己跟。
watch(id, () => void reload())

/** 一条评论的正文。删顶层会连它下面的回复一起删，服务端同事务。 */
async function submitComment(body: string, parentId?: string): Promise<boolean> {
  if (!item.value) return false
  return await store.addComment(item.value.id, body, parentId)
}

/** 视图递上来的一条评论（底部那个框或楼内的一条回复）。结果用回调带回去：发失败了
 *  框和草稿都留着，人按一下就能重试。`emit` 不能 await，所以结果是这么过去的。 */
function onComment(body: string, parentId: string | null, done: (ok: boolean) => void) {
  void submitComment(body, parentId ?? undefined).then(done)
}

/** 支持 / 取消支持。计数交给 store，视图只发一件事。 */
function toggleSupport() {
  if (!item.value) return
  void store.toggleSupport(item.value.id)
}

/** 点赞 / 删除一条评论。走 store，和这一页其余部分一样；评论条只 emit，不发请求。
 *  这里几个包装只做一件事：把「当前这条反馈的 id」补上（详情可能在请求在飞的
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

/** 领取 / 放弃。两个按钮画不画由服务端的 `can_claim` / `can_release` 定（见
 *  `FeedbackClaimFlags`）。成败都重读一遍这条：失败多半是别人刚领走了（409），
 *  屏幕上该换成是谁领着，而服务端那句原话（「已经由 X 领取」）留在错误条里。 */
const claiming = ref(false)

async function changeClaim(take: boolean) {
  if (!item.value || claiming.value) return
  claiming.value = true
  const feedbackId = item.value.id
  let failure: string | null = null
  try {
    await (take ? claimFeedback(feedbackId) : releaseFeedback(feedbackId))
  } catch (error) {
    failure = error instanceof Error && error.message ? error.message : t('feedback.errors.actionFailed')
  }
  await store.loadDetail(feedbackId)
  if (failure) store.error = failure
  claiming.value = false
}

/** 删掉这条反馈。视图只发「删」这件事，确认那一句和按钮状态在它那边；这里落一次
 *  store，删完回中心（这一条已经不存在了，回退键不该回到一个 404）。失败时把结果
 *  交回视图，让它留在原地 —— 服务端的原话就在页面上那块 alert 里。 */
async function doDelete(done: (ok: boolean) => void) {
  if (!item.value) {
    done(false)
    return
  }
  const ok = await store.deleteFeedback(item.value.id)
  done(ok)
  if (ok) backToCenter()
}

async function share() {
  try {
    await navigator.clipboard.writeText(window.location.href)
    toast.success(t('feedback.detail.copied'))
  } catch {
    // 复制失败不弹 toast：剪贴板被拒是环境问题，右侧「已定位」的地址依然可见。
  }
}
</script>

<template>
  <!-- 滚动归这一页自己领（`FeedbackPageShell` 里那一层），理由见 FeedbackCenterPage
       顶部那段注释。这一页用**宽档**（两栏）、底边留白交给页内的黏底元素
       （操作栏和评论框），壳不再自己加。 -->
  <FeedbackPageShell wide flush-bottom>
    <!-- 返回那一条**只在真的有一条反馈时画**：加载中和「这条不存在」两态没有可返回
         的「上一页」这回事（这一页就是它们的落点）。 -->
    <template v-if="item" #head>
      <button class="fb-back" @click="backToCenter()">
        <v-icon size="15" aria-hidden="true">mdi-chevron-left</v-icon>{{ t('feedback.detail.back') }}
      </button>
    </template>

    <FeedbackDetailPageView
      :item="item"
      :loading-detail="store.detailLoading"
      :error="store.error"
      :gone="gone"
      :status-ladder="store.statusLadder"
      :more-comments-loading="store.moreCommentsLoading"
      :more-replies-loading="store.moreRepliesLoading"
      :claiming="claiming"
      :resolve-user="resolveUser"
      @back="backToCenter"
      @dismiss-error="store.clearError()"
      @comment="onComment"
      @toggle-support="toggleSupport"
      @share="share"
      @like="toggleCommentLike"
      @remove-comment="removeComment"
      @load-more-comments="loadMoreComments"
      @load-replies="loadMoreReplies"
      @delete="doDelete"
      @claim="changeClaim(true)"
      @release="changeClaim(false)"
      @navigate="navigate"
    />
  </FeedbackPageShell>
</template>

<style scoped>
/* 「返回」那一条住在页头槽里（`FeedbackPageShell` 的 `#head`），所以它的样式留在
   容器这一侧 —— 视图那一半的样式里没有它。 */
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
</style>
