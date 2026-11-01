<script setup lang="ts">
import type { FeedbackDetail } from '@/cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useFeedbackAttachmentImages } from '@/composables/useFeedbackAttachmentImages'

import FeedbackDetailPageView from './FeedbackDetailPageView.vue'

import { ApiError, getFeedback } from '@/api'
import { claimFeedback, type FeedbackClaimFlags, releaseFeedback } from '@/api/feedbackClaim'
import { t } from '@/i18n'
import { closeOverlay } from '@/lib/backOut'
import { toast } from '@/lib/toast'
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
//
// 画的那一半在 `FeedbackDetailPageView.vue`：这里只留下 store、地址和请求，画面只收
// props、只 emit。评论框的草稿和焦点也留在画面里 —— 它们是那几个元素的状态。
defineOptions({ name: 'FeedbackDetailPage' })

const store = useFeedbackStore()
const route = useRoute()
const router = useRouter()

const id = computed(() => String(route.params.id))
/** 只在这条详情确实是当前这条时才画它。慢响应后到时页面已经换了条目的情况见
 *  `store.loadDetail` 里那个 `detailId` 比较。 */
const item = computed(() => (store.detail?.id === id.value ? store.detail : null))

const posting = ref(false)

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

async function submitComment(body: string, parentId?: string): Promise<boolean> {
  if (!item.value) return false
  return await store.addComment(item.value.id, body, parentId)
}

/** 底部那个输入框。清空和收框是画面那一半的事（草稿住在那儿），所以结果用回调带回去
 *  ——`emit` 不能 await，发失败了框和草稿都得留着。
 *
 *  `posting` 是这一页自己的重入闸：store 的 `addComment` 没有（也不该有）——
 *  它按「一次调用一条评论」办事，而这里连按两下会发两条一模一样的出去。 */
async function postComment(body: string, done: (ok: boolean) => void) {
  if (posting.value) {
    done(false)
    return
  }
  posting.value = true
  try {
    done(await submitComment(body))
  } finally {
    posting.value = false
  }
}

/** 楼内的一条回复。结果同样用回调带回给评论条：发失败了框和草稿都留着，人按一下就能
 *  重试。 */
function submitReply(parentId: string, body: string, done: (ok: boolean) => void) {
  void submitComment(body, parentId).then(done)
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

function toggleSupport() {
  if (!item.value) return
  void store.toggleSupport(item.value.id)
}

/** 删除的**就地确认**：不开弹窗，就地换成「确认 / 取消」两个按钮。
 *
 *  和评论那一条同一个形状（`FeedbackCommentItem`），理由也一样：一次误触的代价是
 *  整条反馈没了，而弹窗会把「我按的是哪一条」这件事从屏幕上挪走。 */
const confirmingDelete = ref(false)
const deletingDelete = ref(false)

async function doDelete() {
  if (!item.value || deletingDelete.value) return
  deletingDelete.value = true
  const ok = await store.deleteFeedback(item.value.id)
  deletingDelete.value = false
  if (!ok) {
    // 失败时**留在原地**：`store.error` 是服务端的原话，它就在上面那块 alert 里，
    // 而这一页还在、人还能重试。跳走等于把失败藏起来。
    confirmingDelete.value = false
    return
  }
  // 删完回反馈中心：这一条已经不存在了，回退键不该回到一个 404。和上面那颗「返回」
  // 同一件事，走同一条路。
  backToCenter()
}

/** 领取 / 放弃。两个按钮画不画由服务端的 `can_claim` / `can_release` 定（见
 *  `FeedbackClaimFlags`）。成败都重读一遍这条：失败多半是别人刚领走了（409），
 *  屏幕上该换成是谁领着，而服务端那句原话（「已经由 X 领取」）留在错误条里。 */
const claim = computed(() => item.value as (FeedbackDetail & FeedbackClaimFlags) | null)
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

/** 附件的字节归这一层取（`useFeedbackAttachmentImages`）：那条端点按这条反馈的可见性
 *  判人，而 `<img src>` 带不上 Authorization，所以要先取回来做成 object URL。画面那一
 *  半只收结果 —— 判据见 `.claude/rules/architecture.md`（组件不取数）。 */
const {
  urls: attachmentUrls,
  downloading: downloadingAttachment,
  download: downloadAttachment,
} = useFeedbackAttachmentImages(() => item.value?.attachments)

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
  <FeedbackDetailPageView
    v-model:confirming-delete="confirmingDelete"
    :item="item"
    :claim="claim"
    :loading="store.detailLoading"
    :error="store.error"
    :gone="gone"
    :status-ladder="store.statusLadder"
    :attachment-urls="attachmentUrls"
    :downloading-attachment="downloadingAttachment"
    :more-comments-loading="store.moreCommentsLoading"
    :more-replies-loading="store.moreRepliesLoading"
    :deleting="deletingDelete"
    :posting="posting"
    :claiming="claiming"
    @back="backToCenter"
    @dismiss-error="store.clearError()"
    @reply="submitReply"
    @like="toggleCommentLike"
    @remove-comment="removeComment"
    @load-more-comments="loadMoreComments"
    @load-more-replies="loadMoreReplies"
    @post="postComment"
    @toggle-support="toggleSupport"
    @share="share"
    @change-claim="changeClaim"
    @delete-confirm="doDelete"
    @download-attachment="downloadAttachment"
  />
</template>
