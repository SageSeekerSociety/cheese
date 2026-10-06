<script setup lang="ts">
import type { FeedbackClaimFlags } from '@/api/feedbackClaim'
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'
import type { FeedbackDetail, FeedbackStatus } from '@/cx_types'

import { computed, nextTick, ref } from 'vue'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import UserRef from '@/components/common/UserRef.vue'
import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import FeedbackClaimCard from '@/components/feedback/FeedbackClaimCard.vue'
import FeedbackCommentsThread from '@/components/feedback/FeedbackCommentsThread.vue'
import FeedbackErrorBanner from '@/components/feedback/FeedbackErrorBanner.vue'
import { kindLabel, sourceLabel } from '@/components/feedback/feedbackLabels'
import FeedbackStatusChip from '@/components/feedback/FeedbackStatusChip.vue'
import FeedbackStatusTimeline from '@/components/feedback/FeedbackStatusTimeline.vue'
import { t } from '@/i18n'
import { isClosed } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'

// 公开反馈详情页 (/feedback/:id) 画的那一半。
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
// **只吃 props、只发事件**：取数、路由、store、领取、评论与删除走的都是容器
// (`FeedbackDetailPage.vue`)。这里面唯一自己拿主意的是纯 UI 状态 —— 评论框收没收、
// 删除确认换没换、正在发没有 —— 它们不跨网络也不碰路由。
const props = defineProps<{
  /** 当前这条详情。为 null 时画加载中或「没读到」。 */
  item: FeedbackDetail | null
  /** 详情正在加载（先画骨架，不先说「暂无」）。 */
  loadingDetail: boolean
  /** 服务端的原话，画在底下那条可关的横幅里。 */
  error: string | null
  /** 「这条不该被看见」（不存在 / 别人的私密反馈）。判据在容器里。 */
  gone: boolean
  /** 服务端给的梯子（有序的状态列表）。 */
  statusLadder: FeedbackStatus[]
  /** 顶层评论正在取下一页。 */
  moreCommentsLoading: boolean
  /** 哪几栋楼正在取楼内的下一页回复，按顶层评论 id。 */
  moreRepliesLoading: Record<string, boolean>
  /** 领取 / 放弃的请求在飞。 */
  claiming: boolean
  /** 人名叫什么、点了去哪。名册与路由都在容器那一侧。 */
  resolveUser: (handle: string | null | undefined, projectId?: string | null) => ResolvedUserRef
}>()

const emit = defineEmits<{
  /** 回反馈中心。 */
  back: []
  /** 关掉底下那条错误横幅。 */
  'dismiss-error': []
  /** 发一条评论（`parentId` 为空就是顶层的）。结果用回调带回，发失败才留草稿。 */
  comment: [body: string, parentId: string | null, done: (ok: boolean) => void]
  /** 支持 / 取消支持这条。 */
  'toggle-support': []
  /** 分享（复制地址）。 */
  share: []
  /** 点赞一条评论。 */
  like: [commentId: string]
  /** 删除一条评论。 */
  'remove-comment': [commentId: string]
  /** 取顶层评论的下一页。 */
  'load-more-comments': []
  /** 取某栋楼回复的下一页。 */
  'load-replies': [parentId: string]
  /** 删除这条反馈。结果用回调带回：失败要留在原地。 */
  delete: [done: (ok: boolean) => void]
  /** 领取。 */
  claim: []
  /** 放弃领取。 */
  release: []
  /** 某个人名被点了。 */
  navigate: [target: ResolvedUserRef['to']]
}>()

const isPrivate = computed(() => props.item?.visibility === 'private')
/** 不能公开的条目（私密 / 安全问题）：没有支持按钮、没有分享。 */
const restricted = computed(() => !!props.item && (isPrivate.value || props.item.security))
const supportable = computed(() => !!props.item && !isClosed(props.item.status))

const commentDraft = ref('')
const posting = ref(false)

/** 底部的评论框收起时只有一行，点开才变成多行框。收起不是图省事：这个框是
 *  `position: sticky` 挂在评论区底部的（理由写在 `.fb-composer` 那条注释里），
 *  一直占着屏幕底下一条，常驻三行加按钮差不多 130px 就是永久少掉的一屏。 */
const composerOpen = ref(false)
const composerToggle = ref<HTMLButtonElement | null>(null)
const composerInput = ref<{ focus: () => void } | null>(null)

/** 拉不到时那一块的两句话（§9.5）。判据只有「服务端认不认这条」一个，由容器给。 */
const missingState = computed(() =>
  props.gone
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

/** 底部那个输入框发成功了才清空 —— 发失败还清掉，等于把刚写的两段话丢掉。
 *  清空是这一半的事（容器只管一条评论的正文）。
 *
 *  `posting` 是这一页自己的重入闸：容器那条发的 action 没有（也不该有）——
 *  它按「一次调用一条评论」办事，而这里连按两下会发两条一模一样的出去。 */
async function postComment() {
  if (posting.value) return
  posting.value = true
  try {
    const draft = commentDraft.value
    const ok = await new Promise<boolean>((done) => emit('comment', draft, null, done))
    if (ok) {
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

/** 楼内的一条回复：线程只往上递，正文与结果的判断都在容器那条 action 上。 */
function onReply(parentId: string, body: string, done: (ok: boolean) => void) {
  emit('comment', body, parentId, done)
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

/** 删除的**就地确认**：不开弹窗，就地换成「确认 / 取消」两个按钮。
 *
 *  和评论那一条同一个形状（`FeedbackCommentItem`），理由也一样：一次误触的代价是
 *  整条反馈没了，而弹窗会把「我按的是哪一条」这件事从屏幕上挪走。 */
const confirmingDelete = ref(false)
const deletingDelete = ref(false)

/** 确认那一句**必须带条数**：「删掉这条反馈」而实际删掉它下面 12 条评论，是在骗
 *  按按钮的人。没有评论时那句只说这一条 —— 凭空多出一个 0 同样是在骗。 */
const deleteAsk = computed(() => {
  const count = props.item?.comments ?? 0
  return count > 0 ? t('feedback.detail.delete.askWithComments', { n: count }) : t('feedback.detail.delete.ask')
})

function doDelete() {
  if (deletingDelete.value) return
  deletingDelete.value = true
  emit('delete', (ok: boolean) => {
    deletingDelete.value = false
    // 失败时**留在原地**：服务端的原话就在上面那块 alert 里，而这一页还在、人还能
    // 重试。跳走等于把失败藏起来。删完回中心是容器的事（这一条已经不存在了）。
    if (!ok) confirmingDelete.value = false
  })
}

/** 领取 / 放弃。两个按钮画不画由服务端的 `can_claim` / `can_release` 定（见
 *  `FeedbackClaimFlags`）。 */
const claim = computed(() => props.item as (FeedbackDetail & FeedbackClaimFlags) | null)
</script>

<template>
  <!-- 还没问出结果之前也画骨架：先画「暂无这条反馈」再换成内容，等于先说错一句
       话再收回去，而这两帧之间在读的人眼里是有先后的。 -->
  <LoadingSkeleton v-if="loadingDetail" variant="detail" :rows="3" />

  <AdminEmptyState
    v-else-if="!item"
    :title="missingState.title"
    :desc="missingState.desc"
    :icon="missingState.icon"
    :tone="gone ? 'neutral' : 'error'"
    :action="t('feedback.detail.missingBack')"
    @action="emit('back')"
  >
    <!-- 服务端那句话照直画出来，但「这条不存在」那一态不画：那句话说的是「这一次
         为什么没拉到」，而在 404 这一态它只会把上面那句换个说法再说一遍。 -->
    <p v-if="!gone" class="fb-state__raw t-meta-read">{{ error }}</p>
  </AdminEmptyState>

  <template v-else>
    <!-- 断点走 CSS 媒体查询（≥1280 双栏），不再经 `useDisplay()`：同一档宽度在
         JS 和 CSS 里各写一遍，两边迟早会分家，而这一页的版式本来就全靠 CSS。 -->
    <div class="fb-layout">
      <!-- 页头（标题 / 状态与标签 / 作者）**单独成一块**，不在 `main` 里面。
           这不是拆得更整齐，是为了手机上能把它排在「进展」前面：进展住在那张
           `aside` 里，而纯 CSS 挪不动 aside —— 只要标题烙在 main 内部，`order` 就只能
           把整块 aside 提到标题**之上**，那比不改还糟。
           窄屏的顺序因此是「页头 → 进展/计数/来源 → 正文 → 评论区」，宽屏（≥1280）
           用 grid-area 把 aside 拉回右栏，屏幕上和改动前一样。 -->
      <div class="fb-head">
        <!-- 标题那一行右侧挂删除。**只在服务端说 `can_delete` 时出现** —— 判据
             （作者或平台管理员）和删那条路由共用一处，所以按钮画得出来就一定删得掉。 -->
        <div v-if="item.can_delete" class="fb-head__row">
          <h1 class="t-page-title fb-title">{{ item.title }}</h1>
          <v-spacer />
          <template v-if="!confirmingDelete">
            <BaseButton size="sm" @click="confirmingDelete = true">
              {{ t('feedback.detail.delete.label') }}
            </BaseButton>
          </template>
          <template v-else>
            <span class="fb-del__ask t-meta-read">{{ deleteAsk }}</span>
            <BaseButton kind="danger" solid size="sm" :loading="deletingDelete" @click="doDelete">
              {{ t('feedback.detail.delete.confirm') }}
            </BaseButton>
            <BaseButton size="sm" @click="confirmingDelete = false">
              {{ t('feedback.detail.delete.cancel') }}
            </BaseButton>
          </template>
        </div>
        <h1 v-else class="t-page-title fb-title">{{ item.title }}</h1>

        <div class="d-flex align-center flex-wrap ga-2 mb-2">
          <FeedbackStatusChip :status="item.status" />
          <span class="chip-neutral">{{ kindLabel(item.kind) }}</span>
          <!-- 私密在详情页比在列表里更要说清楚：读的人可能正是从别处点进来的，
               他需要一眼知道这条没有公开。中性色，和卡片上同一个呈现。 -->
          <span v-if="isPrivate" class="chip-neutral" :title="t('feedback.privateHint')">
            <v-icon size="12">mdi-lock-outline</v-icon>{{ t('feedback.private') }}
          </span>
          <span v-if="item.security" class="chip-neutral">
            <v-icon size="12">mdi-shield-alert-outline</v-icon>{{ t('feedback.security') }}
          </span>
          <span v-if="item.author_is_agent" class="chip-neutral">
            <v-icon size="12">mdi-robot-outline</v-icon>{{ sourceLabel('agent') }}
          </span>
          <span v-for="tag in item.tags" :key="tag" class="chip-neutral">{{ tag }}</span>
        </div>
        <!-- 编号和作者分两行：`FB-1042` 是给人念、给人粘的，作者名之后那一串
             才是「什么时候提的」。挤在一行会让编号看着像作者名的一部分。
             头像是这一页最大的一处（28px）：详情页是唯一一处读者真的会停下来看
             「这是谁提的」的地方，列表里那个 18px 的在这里就太小了。 -->
        <div class="t-meta-read t-num mb-1 d-flex align-center ga-2">
          <FeedbackAuthorAvatar
            :handle="item.author_handle"
            :is-agent="item.author_is_agent"
            :avatar-id="item.author_avatar_id"
            :size="28"
          />
          <span
            ><span class="fb-id">{{ item.display_id }}</span> · {{ item.author_handle }} ·
            {{ relTime(item.created_at) }}</span
          >
        </div>
        <!-- 提案卡发出来的那条有两个名字：agent 找出来的、人发出去的。两个都写，
             因为「这是谁提的」在这条路径上有两个都对但不同的答案。 -->
        <div v-if="item.submitted_by_handle" class="t-meta-read t-num">
          <i18n-t keypath="feedback.detail.submittedBy" tag="span">
            <template #handle
              ><UserRef
                :handle="item.submitted_by_handle"
                :name="resolveUser(item.submitted_by_handle).name"
                :to="resolveUser(item.submitted_by_handle).to"
                @navigate="emit('navigate', resolveUser(item.submitted_by_handle).to)"
            /></template>
          </i18n-t>
        </div>
      </div>

      <main class="fb-main">
        <section v-if="item.problem" class="fb-section">
          <div class="t-eyebrow mb-1">{{ t('feedback.detail.problem') }}</div>
          <p class="t-reading fb-text">{{ item.problem }}</p>
        </section>

        <section v-if="item.why" class="fb-section">
          <div class="t-eyebrow mb-1">{{ t('feedback.detail.why') }}</div>
          <p class="t-reading fb-text">{{ item.why }}</p>
        </section>

        <section v-if="item.expectation" class="fb-section">
          <div class="t-eyebrow mb-1">{{ t('feedback.detail.expectation') }}</div>
          <p class="t-reading fb-text">{{ item.expectation }}</p>
        </section>

        <!-- Agent 发现的那一类：现场三段。人提交的反馈没有这三段，整块不出现。
             三段的小标题写中文，和界面其余部分一致：「REPRO」对第一次看的人来说
             不是一个词（docs/design-system.md §8.0）。 -->
        <section v-if="item.what_happened || item.repro || item.evidence" class="fb-section">
          <div class="t-eyebrow mb-2">{{ t('feedback.detail.context') }}</div>
          <div class="fb-evidence">
            <div v-if="item.what_happened" class="fb-evidence__block">
              <div class="t-eyebrow mb-1">{{ t('feedback.detail.whatHappened') }}</div>
              <p class="t-reading fb-text">{{ item.what_happened }}</p>
            </div>
            <div v-if="item.repro" class="fb-evidence__block">
              <div class="t-eyebrow mb-1">{{ t('feedback.detail.repro') }}</div>
              <pre class="fb-pre">{{ item.repro }}</pre>
            </div>
            <div v-if="item.evidence" class="fb-evidence__block">
              <div class="t-eyebrow mb-1">{{ t('feedback.detail.evidence') }}</div>
              <p class="t-reading fb-text">{{ item.evidence }}</p>
            </div>
            <div v-if="item.session_id || item.environment" class="t-meta fb-evidence__block">
              <template v-if="item.session_id">{{ t('feedback.sessionLine', { id: item.session_id }) }}</template>
              <template v-if="item.session_id && item.environment"> · </template>
              <template v-if="item.environment">{{ item.environment }}</template>
            </div>
          </div>
        </section>

        <section class="fb-section">
          <div class="fb-comments-head">
            <div class="t-eyebrow">{{ t('feedback.detail.commentsLabel', { n: item.comments }) }}</div>
          </div>

          <!-- 所有动作都往上发事件（评论条自己不发请求），容器再走 store。
               点赞不刷新任何 Tab 的计数，删除会同时把楼里的回复从本地摘掉 ——
               两件事的理由都写在 store 里那两条 action 上。

               `has-more` 问的是**服务端**那一页取完没有：本地还剩几条说明不了
               「后面还有没有」，两者是不同的东西。两个 `loading-*` 是这一页
               唯一的重入闸，理由写在 store 上那两条 action 里。 -->
          <FeedbackCommentsThread
            :comments="item.thread"
            :has-more="!!item.thread_next_cursor"
            :loading-more="moreCommentsLoading"
            :loading-replies="moreRepliesLoading"
            :resolve-user="resolveUser"
            @reply="onReply"
            @like="emit('like', $event)"
            @remove="emit('remove-comment', $event)"
            @load-more="emit('load-more-comments')"
            @load-replies="emit('load-replies', $event)"
            @navigate="emit('navigate', $event)"
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
              {{ t('feedback.detail.composer.open') }}
            </button>
            <template v-else>
              <!-- max-rows：autoGrow 是全局默认，而这个框挂在视口底下 ——
                   不封顶的话一段长评论会把整屏顶掉。 -->
              <v-textarea
                ref="composerInput"
                v-model="commentDraft"
                autocomplete="off"
                :placeholder="t('feedback.detail.composer.placeholder')"
                rows="3"
                max-rows="8"
                hide-details
                @blur="onComposerBlur"
              />
              <div class="d-flex justify-end mt-2">
                <BaseButton
                  kind="primary"
                  size="sm"
                  :disabled="!commentDraft.trim()"
                  :loading="posting"
                  @click="postComment"
                >
                  {{ t('feedback.detail.composer.submit') }}
                </BaseButton>
              </div>
            </template>
          </div>
        </section>
      </main>

      <aside class="fb-aside">
        <div class="fb-aside__card">
          <div class="t-eyebrow mb-3">{{ t('feedback.detail.aside.progress') }}</div>
          <FeedbackStatusTimeline
            :timeline="item.timeline"
            :status="item.status"
            :ladder="statusLadder"
            :resolve-user="resolveUser"
            @navigate="emit('navigate', $event)"
          />
        </div>

        <!-- 谁领着这条。没人领、这个读者也领不了的时候不画：那一格对他只是一句
             「还没人领取」，回答的不是他会问的问题。 -->
        <div v-if="claim && (claim.assignee_handle || claim.can_claim)" class="fb-aside__card">
          <FeedbackClaimCard
            :holder="claim.assignee_handle"
            :can-claim="claim.can_claim"
            :can-release="claim.can_release"
            :busy="claiming"
            :resolve-user="resolveUser"
            @claim="emit('claim')"
            @release="emit('release')"
            @navigate="emit('navigate', $event)"
          />
        </div>

        <div class="fb-aside__card">
          <!-- 私密反馈没有「支持人数」这一格：它恒为 0，摆在那里只会让人以为
               「还没人支持」，而不是「这件事对私密反馈不成立」。 -->
          <div v-if="!restricted" class="fb-aside__stat">
            <span class="t-meta-read t-num">{{ t('feedback.detail.aside.supports') }}</span
            ><span class="fb-aside__num">{{ item.supports }}</span>
          </div>
          <div class="fb-aside__stat">
            <span class="t-meta-read t-num">{{ t('feedback.detail.aside.comments') }}</span
            ><span class="fb-aside__num">{{ item.comments }}</span>
          </div>
        </div>

        <!-- 这条反馈是从哪个话题来的。没有话题的那种（harness 在沙箱里撞的墙）
             就没有这一格 —— 那正是它要报的那类问题。 -->
        <div v-if="item.topic_id" class="fb-aside__card">
          <div class="t-eyebrow mb-2">{{ t('feedback.detail.aside.source') }}</div>
          <div class="t-meta-read t-num">{{ t('feedback.detail.aside.topic') }}</div>
        </div>
      </aside>
    </div>

    <!-- 服务端的原话（412 的「已经办完了，不再接受支持」也走这里）。
         可关：这一页上的失败大多是可重试的一次性失败（评论没发出去、下一页没
         取到），一句话挂在页面底部陪着你看完剩下三条评论，读的人只会以为页面
         坏了。关掉它不影响任何状态 —— 没成的操作本来就没改任何东西。 -->
    <FeedbackErrorBanner v-if="error" class="mt-4" :message="error" @dismiss="emit('dismiss-error')" />

    <!-- 64px 粘底操作栏（§4.4）。用户侧这一栏里的主操作是**支持**，这一栏的
         琥珀。它原先是漂在正文中间的一颗按钮，滚过两屏就够不着了，而它是
         这一页唯一一件「读完之后能做的事」；现在它钉在屏幕底下，读到哪里都在。
         分享是次要动作，中性描边。
         「已支持」那一态跟着退回中性 tonal：琥珀画的是「现在该做这件」，而它已经
         做完了。三个不依赖颜色的信号还在（文字、实心图标、计数变 --ink）。
         私密和安全问题整条栏都不画：那两类连支持都不成立（支持是公开表态），
         分享出去的链接对别人也打不开 —— 摆两颗按不动的按钮比不摆更坏。 -->
    <div v-if="!restricted" class="fb-actionbar">
      <BaseButton
        :kind="item.supported ? 'secondary' : 'primary'"
        :prepend-icon="item.supported ? 'mdi-thumb-up' : 'mdi-thumb-up-outline'"
        :disabled="!supportable"
        :title="supportable ? '' : t('feedback.closedHint')"
        @click="emit('toggle-support')"
      >
        {{ item.supported ? t('feedback.detail.action.supported') : t('feedback.detail.action.support') }}
        <span class="fb-support-count">{{ item.supports }}</span>
      </BaseButton>
      <BaseButton kind="secondary" prepend-icon="mdi-share-variant-outline" @click="emit('share')">
        {{ t('feedback.detail.action.share') }}
      </BaseButton>
    </div>
  </template>
</template>

<style scoped>
/* 拉不到时那一块（§9.2 的内容块）：宽 320、水平居中，主文案 15/--lh-15/600/--ink，
   副文案 13/--lh-13/--muted，主副之间 8px。整块不用 --faint：这两句话是要人读的
   （AA 4.5:1），而 --faint 在浅色主题下四种底色上都到不了 3:1。 */
.fb-state__raw {
  margin: 0;
  text-align: center;
  word-break: break-word;
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
.fb-head,
.fb-main,
.fb-aside {
  /* 长 handle 没有空格，不写这条会把轨道撑宽、整页可以横向拖。 */
  min-width: 0;
  /* `width: 100%` 是**必须**的，而且必须三块都有：`justify-self: center` 配一个
     `width: auto` 的 grid item，它先按 max-content 收缩、再在自己的轨道里居中。
     正文那一块因为内容够宽、被 660 的上限顶住，等于占满轨道，所以它看着没事；
     而页头（标题 + 几个 chip + 一行作者）自然宽度只有一百多像素，于是它被居中到
     轨道中间 —— 手机上比正文右偏约 93px、平板上偏 240px，三块东西三个左沿。
     写满宽度之后三块都取 `min(轨道, 660)`，再一起居中，左沿才是一条线。 */
  width: 100%;
  max-width: var(--page-w-read);
  justify-self: center;
}
/* 一栏这一档（<1280）的阅读顺序：**页头 → 进展 → 正文 → 评论**。
   手机上「走到哪一步」原先排在整条评论区之后 —— 要读完所有评论才看得到自己关心的
   那条走到哪了，而它恰好是「我要不要支持」的依据。改的是顺序，不是内容。 */
@media (max-width: 1279.98px) {
  .fb-head {
    order: 1;
  }
  .fb-aside {
    order: 2;
  }
  .fb-main {
    order: 3;
  }
}
@media (min-width: 1280px) {
  .fb-layout {
    grid-template-columns: minmax(0, var(--page-w-read)) 280px;
    /* 页头和正文同住左列、右栏跨两行 —— 和改动前屏幕上看到的一样。 */
    grid-template-areas:
      'head aside'
      'main aside';
    justify-content: space-between;
    gap: 32px;
  }
  /* 两栏这一档，轨道本身已经是那两个宽度（正文正好 --page-w-read），上面那条上限
     留给一栏那一档就好 —— 不留神会把右栏也压成 660 的宽度。
     `justify-self: stretch` 也是给这一档的：上面那条 `center` 是为一栏那一档写的
     （把锁了 660 的正文居中），在网格里留着它会让页头在自己的轨道里居中 —— 而页头
     比轨道窄，于是它和正文的左沿差出一百多像素（真浏览器里量到的就是这一条）。 */
  .fb-head,
  .fb-main,
  .fb-aside {
    max-width: none;
    justify-self: stretch;
  }
  .fb-head {
    grid-area: head;
  }
  .fb-main {
    grid-area: main;
  }
  .fb-aside {
    grid-area: aside;
  }
}
/* 标题 + 删除那一行。标题本来单独占一行；有删除按钮时两者同排，标题照旧由
   `.fb-title` 管自己的字号与下边距。 */
.fb-head__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

/* 确认那一句：和「删除」同一行、贴着按钮，读的人不用在屏幕上找它。 */
.fb-del__ask {
  align-self: center;
}

.fb-title {
  margin-bottom: 8px;
}
.fb-support-count {
  margin-left: 8px;
}
/* 编号是这一行里唯一的标识符，只有它用等宽。 */
.fb-id {
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
  z-index: var(--z-raised-2);
  margin-top: 12px;
  /* 下内边距就是这一页末尾的留白（`.fb-page` 那 48px 挪到这儿了）。 */
  padding: 8px 0 16px;
  background: var(--surface);
}
/* 操作栏不在的页面（私密 / 安全那条），评论框自己就是最底下那件东西，`bottom: 0`
   贴在视口底 —— 让出 `safe-area-inset-bottom`，否则手机上是压着 Home 横杠的。操作
   栏在的时候它已经抬到 64px 上去了，安全区由那条栏自己出，这里不再叠一次。 */
.fb-composer:not(.fb-composer--raised) {
  padding-bottom: calc(16px + env(safe-area-inset-bottom, 0px));
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
  font-variant-numeric: tabular-nums;
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
  z-index: var(--z-raised-3);
  display: flex;
  align-items: center;
  box-sizing: border-box;
  height: 64px;
  padding: 0 16px;
  /* iOS 那条横条压在操作栏上时，底下那颗按钮点不到。安全区只在有它的时候才加，
     没有这个变量时 `0px` 是空操作。 */
  padding-bottom: env(safe-area-inset-bottom, 0);
  gap: 8px;
  /* 只占正文那一栏的宽：它装的是「读完这条之后」的动作，跟着正文走。横跨两栏时，
     短页面上它是一条悬在页面中段、把右栏底下也划掉的白条。一栏那一档正文锁 660
     居中，这里同一个上限、同样居中；两栏那一档左对齐到正文列。 */
  width: 100%;
  max-width: var(--page-w-read);
  margin: 0 auto;
  background: var(--surface);
  border-top: 1px solid var(--line);
}
@media (min-width: 1280px) {
  .fb-actionbar {
    margin-left: 0;
  }
}
</style>
