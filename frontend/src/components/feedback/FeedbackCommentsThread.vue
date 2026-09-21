<script setup lang="ts">
import type { FeedbackComment } from '@/cx_types'

import { computed, ref } from 'vue'

import FeedbackCommentItem from '@/components/feedback/FeedbackCommentItem.vue'

// 评论**只有一种摆法**：两层折叠。
//
// 上一轮这里是三种布局加一个开关（甲·平铺 / 乙·一条流 / 丙·两层折叠），那是给人
// 挑的评审道具，不是产品的一部分。挑中的就是这个 —— 理由不是审美，是**服务端记的
// 就是这一种**：`services.comment` 会把「回复一条回复」折到同一栋楼里（`parent_id`
// 永远指向顶层），所以数据里天然只有两层，另外两版画的层级关系是客户端自己编的。
//
// 顶层评论一条一条排，回复缩进挂在它下面，回复多了先露出两条、其余折在一个
// 「展开更多」后面。第三层永远不会出现，也就没有「无限嵌套之后左边只剩 40px」
// 那个经典问题。
//
// 代价很直白：**破坏时间顺序**。一条十分钟前的顶层评论下面挂着的可能是刚发的回复，
// 而它上面那条顶层评论是两小时前的。要按时间读的话，看右侧的「进展」卡。
//
// 「展开更多」不是「加载更多」：服务端一次把整条反馈的评论都给全了（`thread`），
// 这里折的是**已经拿到手的**那几条，不发第二个请求。所以文案不写「加载」。
// 默认露出两条而不是全部展开：一栋热议的楼会把下面所有评论推到屏幕外，读的人
// 以为这条反馈就这么长。而全折起来（上一版）又会让他先点一下才看得见「回复挂在
// 哪」—— 那正是这一版要表达的东西。两条是这两件事之间的落点，和 B站/小红书一致。
const props = defineProps<{ comments: FeedbackComment[] }>()

const emit = defineEmits<{
  reply: [parentId: string, body: string]
  like: [commentId: string]
  remove: [commentId: string]
}>()

/** 一栋楼默认露出几条回复。 */
const INITIAL_REPLIES = 2

const tops = computed(() => props.comments.filter((c) => !c.parent_id))

function repliesOf(id: string): FeedbackComment[] {
  return props.comments.filter((c) => c.parent_id === id)
}

/** 展开了的楼。 */
const expanded = ref<Record<string, boolean>>({})

function shownOf(id: string): FeedbackComment[] {
  const all = repliesOf(id)
  // 顺序是服务端的（created_at asc, id asc），展开露出的是**靠后的**那几条 ——
  // 顺着往下读正好接上，不会在「展开更多」上面插出一段更早的。
  return expanded.value[id] ? all : all.slice(0, INITIAL_REPLIES)
}

/** 正在回复谁。同一时刻只开一个输入框 —— 楼里几个框同时开着，读的人分不清哪个
 *  发到哪。开在谁身上由这里决定，评论条自己不知道。 */
const replyTo = ref<string | null>(null)

function toggleReply(commentId: string) {
  replyTo.value = replyTo.value === commentId ? null : commentId
}

function send(parentId: string, body: string) {
  emit('reply', parentId, body)
  replyTo.value = null
  // 刚回完的那栋楼一定要整个展开：回复发出去看不见，等于没发出去。
  expanded.value[parentId] = true
}

function onLike(commentId: string) {
  emit('like', commentId)
}

function onRemove(commentId: string) {
  emit('remove', commentId)
  // 删顶层会连它下面的回复一起没。回复框如果正开在这一条上，它指着的是一条马上
  // 就不存在的评论 —— 关掉，别让它留成一个发不出去的框。
  if (replyTo.value === commentId) replyTo.value = null
}
</script>

<template>
  <div v-if="tops.length" class="fb-thread">
    <div v-for="top in tops" :key="top.id" class="fb-thread__top">
      <FeedbackCommentItem
        :comment="top"
        :replying="replyTo === top.id"
        :reply-count="repliesOf(top.id).length"
        @toggle-reply="toggleReply"
        @reply="send"
        @like="onLike"
        @remove="onRemove"
      />

      <!-- 回复区。左边那根竖线是**缩进的说明**，不是装饰：没有它，24px 的缩进在
           长评论之间会看不出来。回复之间不画分隔线，靠间距和缩进分块。 -->
      <div v-if="repliesOf(top.id).length" class="fb-thread__replies">
        <FeedbackCommentItem
          v-for="reply in shownOf(top.id)"
          :key="reply.id"
          :comment="reply"
          is-reply
          :replying="replyTo === reply.id"
          :reply-count="0"
          @toggle-reply="toggleReply"
          @reply="send"
          @like="onLike"
          @remove="onRemove"
        />

        <button
          v-if="repliesOf(top.id).length > INITIAL_REPLIES"
          type="button"
          class="fb-thread__more"
          @click="expanded[top.id] = !expanded[top.id]"
        >
          <v-icon size="14">{{ expanded[top.id] ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
          {{ expanded[top.id] ? '收起' : `展开更多 ${repliesOf(top.id).length - INITIAL_REPLIES} 条回复` }}
        </button>
      </div>
    </div>
  </div>
  <div v-else class="t-body c-faint mb-3">暂无评论</div>
</template>

<style scoped>
.fb-thread {
  display: flex;
  flex-direction: column;
  margin-bottom: 24px;
}
.fb-thread__top + .fb-thread__top {
  margin-top: 16px;
}
/* 缩进 24px = 3 格（8px 网格）。左边那根线画在缩进的起点上，所以它读起来是
   「这些是楼上那条的回复」，而不是一条装饰性的竖线。 */
.fb-thread__replies {
  margin: 8px 0 0 4px;
  padding-left: 20px;
  border-left: 1px solid var(--line);
}
.fb-thread__replies > * + * {
  margin-top: 12px;
}
.fb-thread__more {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 6px;
  margin-left: -6px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  cursor: pointer;
}
.fb-thread__more:hover {
  background: var(--fill-2);
  color: var(--ink);
}
</style>
