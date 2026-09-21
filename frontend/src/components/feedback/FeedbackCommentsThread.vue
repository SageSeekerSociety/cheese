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
// **「展开更多」和「加载更多」是两件事，因为评论是分页取的**：
//
// - 「展开更多 N 条回复」= 手上已经有这些条，只是折着 —— 不发请求。
// - 「加载更多回复」= 这一栋楼里服务端还有，得去取下一页 —— 发请求。
//
// 两者怎么区分由**服务端的那个数**说了算：`reply_count` 是这栋楼一共几条回复，
// 手上拿了几条是可以数出来的，两个一比就知道还有没有。客户端不自己猜「大概取完了
// 吧」—— 一栋正好一整页的楼会被猜成还有下一页，于是发一次必然取到空页的请求。
//
// 顶层评论也是分页的，往下的按钮同理：`hasMore` 由服务端的游标给。
//
// 默认露出两条而不是全部展开：一栋热议的楼会把下面所有评论推到屏幕外，读的人
// 以为这条反馈就这么长。而全折起来（上一版）又会让他先点一下才看得见「回复挂在
// 哪」—— 那正是这一版要表达的东西。两条是这两件事之间的落点，和 B站/小红书一致。
const props = defineProps<{
  comments: FeedbackComment[]
  /** 顶层评论还有下一页（服务端的游标还没走到底）。 */
  hasMore: boolean
  /** 顶层评论正在取下一页。 */
  loadingMore: boolean
  /** 哪几栋楼正在取楼内的下一页回复，按顶层评论 id。同一栋楼两次点击只会发一次
   *  请求 —— 两次会把同一段回复追加两遍，屏幕上是两条一模一样的回复。 */
  loadingReplies: Record<string, boolean>
}>()

const emit = defineEmits<{
  reply: [parentId: string, body: string, done: (ok: boolean) => void]
  like: [commentId: string]
  remove: [commentId: string]
  'load-more': []
  'load-replies': [parentId: string]
}>()

/** 一栋楼默认露出几条回复。 */
const INITIAL_REPLIES = 2

const tops = computed(() => props.comments.filter((c) => !c.parent_id))

function repliesOf(id: string): FeedbackComment[] {
  return props.comments.filter((c) => c.parent_id === id)
}

/** 服务端说这栋楼一共几条回复。取 `max` 是防御性的：手上已经数出来的条数永远不该
 *  被一个过期的总数盖住（那会让「还有 3 条」变成负数，或者让已取到的回复看不见）。 */
function totalRepliesOf(top: FeedbackComment): number {
  return Math.max(top.reply_count, repliesOf(top.id).length)
}

/** 展开了的楼。 */
const expanded = ref<Record<string, boolean>>({})

function shownOf(id: string): FeedbackComment[] {
  const all = repliesOf(id)
  // 顺序是服务端的（created_at asc, id asc）：折起来时露出**最早**的两条，展开
  // 往后接着排，下一页再往后接 —— 顺着往下读正好接上。
  return expanded.value[id] ? all : all.slice(0, INITIAL_REPLIES)
}

/** 楼下那个按钮此刻该做什么。三种，且只有一种。 */
type MoreAction = { kind: 'expand'; hidden: number } | { kind: 'load'; remaining: number } | { kind: 'collapse' }

function moreOf(top: FeedbackComment): MoreAction | null {
  const loaded = repliesOf(top.id).length
  const total = totalRepliesOf(top)
  if (expanded.value[top.id]) {
    // 摊开了：还有没取的就去取，取完了才轮到「收起」。
    if (loaded < total) return { kind: 'load', remaining: total - loaded }
    return loaded > INITIAL_REPLIES ? { kind: 'collapse' } : null
  }
  const hidden = loaded - INITIAL_REPLIES
  if (hidden > 0) return { kind: 'expand', hidden }
  if (loaded < total) return { kind: 'load', remaining: total - loaded }
  return null
}

function moreLabel(action: MoreAction | null): string {
  if (!action) return ''
  switch (action.kind) {
    case 'expand':
      return `展开更多 ${action.hidden} 条回复`
    case 'load':
      return `加载更多回复（还有 ${action.remaining} 条）`
    case 'collapse':
      return '收起'
  }
}

/** 每栋楼此刻的按钮动作，按顶层评论 id。算一次给模板用三处（画不画、图标朝哪、
 *  写什么字）—— 模板里连写三遍 `moreOf(top)` 是三遍全列表扫描，而且三处各算各的
 *  迟早会有一天不一致。 */
const moreActions = computed(() => {
  const map: Record<string, MoreAction | null> = {}
  for (const top of tops.value) map[top.id] = moreOf(top)
  return map
})

function onMore(top: FeedbackComment) {
  const action = moreOf(top)
  if (!action) return
  if (action.kind === 'collapse') {
    expanded.value[top.id] = false
    return
  }
  if (action.kind === 'expand') {
    expanded.value[top.id] = true
    return
  }
  if (props.loadingReplies[top.id]) return
  // 一栋楼打开着才可能点「加载更多」，所以这里顺手记成展开：否则取回来的那几条
  // 会落在一个折着的楼里，屏幕上什么都没变，看起来像请求没生效。
  expanded.value[top.id] = true
  emit('load-replies', top.id)
}

/** 正在回复谁。同一时刻只开一个输入框 —— 楼里几个框同时开着，读的人分不清哪个
 *  发到哪。开在谁身上由这里决定，评论条自己不知道。 */
const replyTo = ref<string | null>(null)

function toggleReply(commentId: string) {
  replyTo.value = replyTo.value === commentId ? null : commentId
}

function send(parentId: string, body: string, done: (ok: boolean) => void) {
  emit('reply', parentId, body, (ok: boolean) => {
    // 发失败了：框留着、草稿留着（评论条自己管），人只要按一下重试，不用把刚写的
    // 那段话再打一遍。发成功了才收框，并且把那一栋整个展开 —— 回复发出去看不见，
    // 等于没发出去。
    if (!ok) {
      done(false)
      return
    }
    replyTo.value = null
    expanded.value[parentId] = true
    done(true)
  })
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
  <!-- 列表用 `ul/li` 而不是一摞 `div`：读屏会报「列表，20 项」，也能跳着读；
      `div` 那一版在听力上是一团没有边界的话。样式上唯一的代价是把 list-style
      和自带缩进清掉。 -->
  <ul v-if="tops.length" class="fb-thread">
    <li v-for="top in tops" :key="top.id" class="fb-thread__top">
      <FeedbackCommentItem
        :comment="top"
        :replying="replyTo === top.id"
        :reply-count="totalRepliesOf(top)"
        @toggle-reply="toggleReply"
        @reply="send"
        @like="onLike"
        @remove="onRemove"
      />

      <!-- 回复区。左边那根竖线是**缩进的说明**，不是装饰：没有它，24px 的缩进在
           长评论之间会看不出来。回复之间不画分隔线，靠间距和缩进分块。 -->
      <ul v-if="repliesOf(top.id).length" class="fb-thread__replies">
        <li v-for="reply in shownOf(top.id)" :key="reply.id">
          <FeedbackCommentItem
            :comment="reply"
            is-reply
            :replying="replyTo === reply.id"
            :reply-count="0"
            @toggle-reply="toggleReply"
            @reply="send"
            @like="onLike"
            @remove="onRemove"
          />
        </li>
      </ul>

      <button
        v-if="moreActions[top.id]"
        type="button"
        class="fb-thread__more"
        :disabled="!!loadingReplies[top.id]"
        @click="onMore(top)"
      >
        <v-icon :size="14">{{
          moreActions[top.id]?.kind === 'collapse' ? 'mdi-chevron-up' : 'mdi-chevron-down'
        }}</v-icon>
        {{ loadingReplies[top.id] ? '正在加载…' : moreLabel(moreActions[top.id]) }}
      </button>
    </li>

    <li v-if="hasMore">
      <button type="button" class="fb-thread__more" :disabled="loadingMore" @click="emit('load-more')">
        <v-icon size="14">mdi-chevron-down</v-icon>
        {{ loadingMore ? '正在加载…' : '加载更多评论' }}
      </button>
    </li>
  </ul>
  <div v-else class="t-body c-faint mb-3">暂无评论</div>
</template>

<style scoped>
.fb-thread {
  display: flex;
  flex-direction: column;
  margin: 0 0 24px;
  padding: 0;
  list-style: none;
}
.fb-thread__top + .fb-thread__top {
  margin-top: 16px;
}
/* 缩进 24px = 3 格（8px 网格）：线画在 8px 上，回复的正文从 24px 起。线的位置
   就是「缩进」这个动作本身，所以它不是装饰性的竖线。 */
.fb-thread__replies {
  margin: 8px 0 0 8px;
  padding-left: 16px;
  border-left: 1px solid var(--line);
  list-style: none;
}
.fb-thread__replies > li + li {
  margin-top: 12px;
}
/* 「展开更多 / 加载更多 / 收起」三个状态共用这一个按钮：它是楼内的一句说明加一个
   动作，不是主操作，所以中性色。负的左边距把按钮自己的内边距拉回去，标签和上面
   的正文对齐。 */
.fb-thread__more {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  padding: 4px 8px;
  margin-left: -8px;
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
.fb-thread__more:disabled {
  cursor: default;
  color: var(--faint);
}
.fb-thread__more:disabled:hover {
  background: transparent;
  color: var(--faint);
}
</style>
