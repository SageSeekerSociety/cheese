<script setup lang="ts">
import type { FeedbackComment } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import { relTime } from '@/lib/relTime'

// 一条评论。顶层评论和楼内回复**共用这一个组件**，因为读法必须一致：谁写的、
// 什么时候、有多少人赞、回的是谁，这四件事在两种位置上答案不同但问法一样。
// 这里分开写一遍的代价在别处已经付过一次了 —— 列表卡片和详情页各写一遍「已上线
// 还能不能支持」，两处一起错。所以差异只走参数：`isReply` 只影响头像尺寸和
// 「回复 X」那一行的有无，判断本身没有第二份。
//
// 三个动作的摆法：
//
// - **点赞是中性色**。激活态是「底色出现 + 图标实心 + 文案从『赞』变『已赞』」
//   三个信号一起变，颜色只是其中之一 —— 只靠底色的话，色觉障碍的读者看不出
//   自己点没点过。琥珀（--accent）留给这一页唯一的主操作「发表评论」，不撒到
//   评论区来（docs/design-system.md §0）。
// - **『回复 X』只在数据里有的时候才画**。`reply_to_handle` 是服务端折楼的时候
//   存下来的快照：`parent_id` 永远指向顶层，所以「回的是楼里哪一条」在折过之后
//   只能从这一列读，前端猜不出来。顶层评论恒为 NULL，因为它没有回复对象。
// - **删除要问一句，而且要说清会连带什么**。删顶层评论会连它下面的回复一起删
//   （服务端同事务软删，否则那些回复会变成查不到父亲的孤儿），所以确认那一句
//   必须带上条数 —— 只说「删掉这条评论」而实际删掉一栋楼，是在骗按按钮的人。
//   按钮本身出不出来由服务端的 `can_delete` 说了算，前端不自己判一遍。
const props = defineProps<{
  comment: FeedbackComment
  /** 这一条是不是正在被回复。同一个时刻线程只让一条为真（见 FeedbackCommentsThread）。 */
  replying: boolean
  /** 这一条是楼内回复。只改头像尺寸和缩进那一行的有无。 */
  isReply?: boolean
  /** 删掉这一条会连带删掉几条回复。顶层的用，回复恒为 0。 */
  replyCount: number
}>()

const emit = defineEmits<{
  /** 请线程把回复框开在这一条上（再点一次是收起来）。开在谁身上是线程的决定。 */
  'toggle-reply': [commentId: string]
  reply: [commentId: string, body: string]
  like: [commentId: string]
  remove: [commentId: string]
}>()

/** 正在确认删除。就地换成「确认 / 取消」两个按钮，不开弹窗：一次误触的代价是
 *  一条评论，而弹窗会把这一页的注意力整块拿走。 */
const confirming = ref(false)

const draft = ref('')

// 回复框被线程关掉（点了别的评论的「回复」、或者这一条刚被删）时把草稿丢掉，
// 否则下次打开还留着上一次的半句话，看着像自己写的又没发出去。
watch(
  () => props.replying,
  (on) => {
    if (!on) draft.value = ''
  }
)

function send() {
  if (!draft.value.trim()) return
  emit('reply', props.comment.id, draft.value)
  draft.value = ''
}

/** 点赞按钮上的字。**文案本身是三个非颜色信号之一**，所以点过和没点过是两个词。
 *  计数单独一格，且只在有人赞过的时候出现 —— 「赞 0」里的 0 会被读成「有人踩过」。 */
const likeLabel = computed(() => (props.comment.liked ? '已赞' : '赞'))

const removeQuestion = computed(() =>
  props.replyCount > 0 ? `删掉这条评论，连同它下面的 ${props.replyCount} 条回复一起？` : '删掉这条评论？'
)

function confirmRemove() {
  confirming.value = false
  emit('remove', props.comment.id)
}
</script>

<template>
  <div class="fb-ci" :class="{ 'fb-ci--reply': isReply }">
    <div class="fb-ci__head">
      <FeedbackAuthorAvatar
        :handle="comment.author_handle"
        :is-agent="comment.author_is_agent"
        :avatar-id="comment.author_avatar_id"
        :size="isReply ? 20 : 22"
      />
      <span class="fb-ci__author">{{ comment.author_handle }}</span>
      <span v-if="comment.author_is_agent" class="chip-neutral">AI 队友</span>
      <span class="t-meta">{{ relTime(comment.created_at) }}</span>
    </div>

    <!-- 回的是谁。单独一行而不是塞进正文前面：正文是用户写的多段文字（保留换行），
         把「回复 X」拼进去会让第一段被挤变形，也让人分不清这句是谁写的。 -->
    <div v-if="comment.reply_to_handle" class="fb-ci__re">
      回复 <span class="fb-ci__re-name">{{ comment.reply_to_handle }}</span>
    </div>

    <p class="t-body fb-ci__body">{{ comment.body }}</p>

    <div v-if="confirming" class="fb-ci__actions">
      <span class="fb-ci__confirm-q">{{ removeQuestion }}</span>
      <button type="button" class="fb-ci__act fb-ci__act--danger" @click="confirmRemove">确认删除</button>
      <button type="button" class="fb-ci__act" @click="confirming = false">取消</button>
    </div>

    <div v-else class="fb-ci__actions">
      <button
        type="button"
        class="fb-ci__act fb-ci__like"
        :class="{ 'is-on': comment.liked }"
        :aria-pressed="comment.liked"
        :title="comment.liked ? '取消点赞' : '点赞这条评论'"
        @click="emit('like', comment.id)"
      >
        <v-icon size="14">{{ comment.liked ? 'mdi-thumb-up' : 'mdi-thumb-up-outline' }}</v-icon>
        {{ likeLabel }}
        <span v-if="comment.likes > 0" class="fb-ci__count">{{ comment.likes }}</span>
      </button>

      <button type="button" class="fb-ci__act" @click="emit('toggle-reply', comment.id)">
        <v-icon size="14">mdi-reply-outline</v-icon>
        {{ replying ? '收起' : '回复' }}
      </button>

      <button v-if="comment.can_delete" type="button" class="fb-ci__act fb-ci__act--danger" @click="confirming = true">
        删除
      </button>
    </div>

    <div v-if="replying" class="fb-ci__form">
      <v-textarea
        v-model="draft"
        autocomplete="off"
        :placeholder="`回复 ${comment.author_handle}`"
        rows="2"
        density="compact"
        hide-details
      />
      <!-- 楼内回复用中性色，不用琥珀：这一页唯一的主操作是底部的「发表评论」，
           琥珀一次只能出现在一个地方（docs/design-system.md §0）。 -->
      <div class="fb-ci__form-actions">
        <v-btn variant="text" color="secondary" size="x-small" @click="emit('toggle-reply', comment.id)"> 取消 </v-btn>
        <v-btn variant="tonal" color="secondary" size="x-small" :disabled="!draft.trim()" @click="send"> 回复 </v-btn>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fb-ci {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.fb-ci__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.fb-ci__author {
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--ink);
}
.fb-ci__re {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.fb-ci__re-name {
  font-weight: 600;
  color: var(--ink);
}
.fb-ci__body {
  margin: 0;
  white-space: pre-wrap;
}
/* 动作行。三个都是文字按钮不是链接：点赞就地变、回复就地展开输入框、删除就地
   换成确认，谁都不跳转。 */
.fb-ci__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}
.fb-ci__act {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  cursor: pointer;
}
.fb-ci__act:hover {
  background: var(--fill-2);
  color: var(--ink);
}
/* 激活态：底色出现（中性 tonal，不是琥珀）+ 图标实心 + 文案变「已赞」。 */
.fb-ci__like.is-on {
  background: var(--line-2);
  color: var(--ink);
}
.fb-ci__like.is-on:hover {
  background: var(--line-2);
}
.fb-ci__count {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
/* 删除的墨色用 --danger-ink（文字那一档），不是 --danger —— 后者是画记号的
   颜色（点、边框、图标），拿它写字在浅色下读不清（docs/design-system.md §1）。 */
.fb-ci__act--danger {
  color: var(--danger-ink);
}
.fb-ci__act--danger:hover {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.fb-ci__confirm-q {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--text);
}
.fb-ci__form {
  margin-top: 4px;
}
.fb-ci__form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
</style>
