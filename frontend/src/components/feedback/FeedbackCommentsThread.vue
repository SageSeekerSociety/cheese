<script setup lang="ts">
import type { FeedbackComment } from '@/cx_types'

import { computed, ref } from 'vue'

import { relTime } from '@/lib/relTime'

// 评论**只有一种摆法**：两层折叠。
//
// 上一轮这里是三种布局加一个开关（甲·平铺 / 乙·一条流 / 丙·两层折叠），那是给人
// 挑的评审道具，不是产品的一部分。挑中的就是这个 —— 理由不是审美，是**服务端记的
// 就是这一种**：`services.comment` 会把「回复一条回复」折到同一栋楼里（`parent_id`
// 永远指向顶层），所以数据里天然只有两层，另外两版画的层级关系是客户端自己编的。
//
// 顶层评论一条一条排，回复缩进挂在它下面，回复多了先折起来。第三层永远不会出现，
// 也就没有「无限嵌套之后左边只剩 40px」那个经典问题。
//
// 代价很直白：**破坏时间顺序**。一条十分钟前的顶层评论下面挂着的可能是刚发的回复，
// 而它上面那条顶层评论是两小时前的。要按时间读的话，看右侧的「进展」卡。
//
// 每条评论都能回：顶层的回复进自己的楼里，楼里再回复也还在这栋楼里（会显示成
// 「回复 X」，因为同一层里说不清是谁回谁）。
const props = defineProps<{ comments: FeedbackComment[] }>()

const emit = defineEmits<{ reply: [parentId: string, body: string] }>()

const tops = computed(() => props.comments.filter((c) => !c.parent_id))
const repliesOf = (id: string) => props.comments.filter((c) => c.parent_id === id)

/** 折起来的楼。默认全展开：这一版的全部价值就是「回复挂在哪」一眼看得见，一进来
 *  就收起来等于让人先点一下才看得到它想表达的东西。 */
const folded = ref<Record<string, boolean>>({})
function toggle(id: string) {
  folded.value[id] = !folded.value[id]
}

/** 正在回复谁（评论 id）。同一时刻只开一个输入框。 */
const replyTo = ref<string | null>(null)
const replyDraft = ref('')

function startReply(id: string) {
  if (replyTo.value === id) {
    replyTo.value = null
    return
  }
  replyTo.value = id
  replyDraft.value = ''
}

function send(parentId: string) {
  if (!replyDraft.value.trim()) return
  emit('reply', parentId, replyDraft.value)
  replyDraft.value = ''
  replyTo.value = null
  // 刚回完的那栋楼一定要是展开的，否则回复发出去看不见。
  folded.value[parentId] = false
}
</script>

<template>
  <div v-if="tops.length" class="fb-thread">
    <div v-for="top in tops" :key="top.id" class="fb-thread__top">
      <div class="fb-thread__item">
        <div class="fb-thread__head">
          <span class="fb-thread__author">{{ top.author_handle }}</span>
          <span v-if="top.author_is_agent" class="chip-neutral">AI 队友</span>
          <span class="t-meta">{{ relTime(top.created_at) }}</span>
          <v-spacer />
          <button class="fb-thread__act" @click="startReply(top.id)">回复</button>
        </div>
        <p class="t-body fb-thread__body">{{ top.body }}</p>

        <div v-if="replyTo === top.id" class="fb-thread__form">
          <v-textarea
            v-model="replyDraft"
            autocomplete="off"
            :placeholder="`回复 ${top.author_handle}`"
            rows="2"
            density="compact"
            hide-details
          />
          <div class="fb-thread__form-actions">
            <v-btn variant="text" color="secondary" size="x-small" @click="replyTo = null">取消</v-btn>
            <v-btn
              variant="tonal"
              color="secondary"
              size="x-small"
              :disabled="!replyDraft.trim()"
              @click="send(top.id)"
            >
              回复
            </v-btn>
          </div>
        </div>
      </div>

      <!-- 回复区。左边那根竖线是**缩进的说明**，不是装饰：没有它，24px 的缩进在
           长评论之间会看不出来。 -->
      <div v-if="repliesOf(top.id).length" class="fb-thread__replies">
        <button class="fb-thread__fold" @click="toggle(top.id)">
          <v-icon size="14">{{ folded[top.id] ? 'mdi-chevron-right' : 'mdi-chevron-down' }}</v-icon>
          {{ folded[top.id] ? `${repliesOf(top.id).length} 条回复` : `收起 ${repliesOf(top.id).length} 条回复` }}
        </button>

        <template v-if="!folded[top.id]">
          <div v-for="reply in repliesOf(top.id)" :key="reply.id" class="fb-thread__item">
            <div class="fb-thread__head">
              <span class="fb-thread__author">{{ reply.author_handle }}</span>
              <span v-if="reply.author_is_agent" class="chip-neutral">AI 队友</span>
              <span class="t-meta">{{ relTime(reply.created_at) }}</span>
              <v-spacer />
              <button class="fb-thread__act" @click="startReply(reply.id)">回复</button>
            </div>
            <p class="t-body fb-thread__body">{{ reply.body }}</p>

            <div v-if="replyTo === reply.id" class="fb-thread__form">
              <v-textarea
                v-model="replyDraft"
                autocomplete="off"
                :placeholder="`回复 ${reply.author_handle}`"
                rows="2"
                density="compact"
                hide-details
              />
              <!-- 楼内回复用中性色，不用琥珀：这一页唯一的主操作是底部的「发表评论」，
                   琥珀一次只能出现在一个地方（docs/design-system.md §0）。 -->
              <div class="fb-thread__form-actions">
                <v-btn variant="text" color="secondary" size="x-small" @click="replyTo = null">取消</v-btn>
                <v-btn
                  variant="tonal"
                  color="secondary"
                  size="x-small"
                  :disabled="!replyDraft.trim()"
                  @click="send(reply.id)"
                >
                  回复
                </v-btn>
              </div>
            </div>
          </div>
        </template>
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
.fb-thread__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.fb-thread__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.fb-thread__author {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.fb-thread__body {
  margin: 0;
  white-space: pre-wrap;
}
/* 「回复」是个文字按钮，不是链接：它触发的是就地展开一个输入框，不跳转。 */
.fb-thread__act {
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
}
.fb-thread__act:hover {
  color: var(--ink);
}
.fb-thread__replies {
  margin: 8px 0 0 4px;
  padding-left: 20px;
  border-left: 1px solid var(--line);
}
.fb-thread__replies .fb-thread__item + .fb-thread__item {
  margin-top: 12px;
}
.fb-thread__fold {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
}
.fb-thread__fold:hover {
  color: var(--ink);
}
.fb-thread__form {
  margin-top: 8px;
}
.fb-thread__form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
</style>
