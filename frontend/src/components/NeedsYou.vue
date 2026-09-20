<script setup lang="ts">
// 等你决定 —— 芝士 问了你一句话，在等你回答。
//
// 这一块从「总览」搬到项目首页：它是那一页上唯一一处别处没有的东西，而那一页答
// 的其他问题（现在轮到谁、交出去了什么、成员是谁）首页和成员页各自答得更准。
//
// 和下面那块板不是一回事：板上的「待处理」是派出去的活轮到你，这里是一条**问题**
// ——选项摆在那儿，你点一个它才算完。所以它按项目的收件箱读（决策请求在被答复之前
// 不会消失），而不是按任务读。
//
// 空的时候整块不出现：没人在等你，首页就不该多一块写着「暂无」的框。
import type { InboxItem } from '@/cx_types'

import { ref, watch } from 'vue'

import { getInbox, markRead, resolveAlert, sendFeedback } from '@/api'
import { label, NOTIF_KIND } from '@/labels'
import { myHandle } from '@/me'

const props = defineProps<{ projectId: string }>()

const rows = ref<InboxItem[]>([])
const actionError = ref('')
const busy = ref('')

/** 这一条给的选项。带选项的才答得了，其余只能读完收起来。 */
function optionsOf(row: InboxItem): string[] {
  const listed = row.payload?.options
  return Array.isArray(listed) ? listed.filter((option): option is string => typeof option === 'string') : []
}

async function load() {
  const projectId = props.projectId
  const me = myHandle()
  if (!me) {
    rows.value = []
    return
  }
  try {
    const listed = await getInbox(projectId, me)
    if (props.projectId !== projectId) return
    rows.value = listed.data
  } catch {
    // 读不到收件箱不该把首页变成一条错误：板是这一页的主体。下一次进来再试一遍。
    if (props.projectId === projectId) rows.value = []
  }
}

async function act(row: InboxItem, run: () => Promise<unknown>, failed: string) {
  busy.value = row.id
  actionError.value = ''
  try {
    await run()
    await load()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : failed
  } finally {
    busy.value = ''
  }
}

/** 拍板。答复之后这一条不再等人，于是整条从这里消失。 */
async function decide(row: InboxItem, chosen: string) {
  await act(row, () => resolveAlert(row.id, chosen), '未能答复')
}

async function dismiss(row: InboxItem) {
  await act(row, () => markRead(row.id), '未能收起')
}

async function rate(row: InboxItem, feedback: 'up' | 'down') {
  await act(row, () => sendFeedback(row.id, feedback), '未能提交反馈')
}

watch(
  () => props.projectId,
  () => {
    rows.value = []
    actionError.value = ''
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <section v-if="rows.length" class="asked">
    <h2 class="asked__title t-title">
      等你决定
      <span class="asked__count t-meta c-faint">{{ rows.length }}</span>
    </h2>
    <p v-if="actionError" role="alert" class="asked__error t-meta">{{ actionError }}</p>
    <ul class="asked__list">
      <li v-for="row in rows" :key="row.id" class="asked-row">
        <div class="asked-row__head">
          <span class="asked-row__kind t-meta c-faint">{{ label(NOTIF_KIND, row.kind) }}</span>
          <span class="asked-row__title t-body">{{ row.title }}</span>
        </div>
        <p v-if="row.body" class="asked-row__body t-meta c-muted">{{ row.body }}</p>
        <div class="asked-row__acts">
          <v-btn
            v-for="option in optionsOf(row)"
            :key="option"
            size="small"
            variant="outlined"
            color="primary"
            :loading="busy === row.id"
            @click="decide(row, option)"
          >
            {{ option }}
          </v-btn>
          <v-btn
            v-if="!optionsOf(row).length"
            size="small"
            variant="text"
            color="on-surface-variant"
            :loading="busy === row.id"
            @click="dismiss(row)"
          >
            知道了
          </v-btn>
          <v-spacer />
          <v-btn
            icon="mdi-thumb-up-outline"
            size="x-small"
            variant="text"
            :color="row.feedback === 'up' ? 'primary' : 'on-surface-variant'"
            aria-label="有帮助"
            @click="rate(row, 'up')"
          />
          <v-btn
            icon="mdi-thumb-down-outline"
            size="x-small"
            variant="text"
            :color="row.feedback === 'down' ? 'primary' : 'on-surface-variant'"
            aria-label="没帮助"
            @click="rate(row, 'down')"
          />
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
/* 和「做出了什么」、下面那块板左右对齐：板自己 12px，头部再补 10px。 */
.asked {
  flex: 0 0 auto;
  padding: 0 10px 14px;
}
.asked__title {
  margin: 0 0 8px;
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.asked__count {
  font-variant-numeric: tabular-nums;
}
/* 错误是给人读的一行字，所以用墨色那一档：`--danger` 是记号的颜色，写字读不出来。 */
.asked__error {
  margin: 0 0 8px;
  color: var(--danger-ink);
}
.asked__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
/* 一条是一个问题：上面是它问了什么，下面是答它的那几个按钮。左边那道暖色竖线和
   看板里「待处理」那一列同一个 token——整个首页上「该你动手了」只有这一种颜色。 */
.asked-row {
  padding: 10px 10px 6px 12px;
  border: 1px solid var(--line);
  border-left: 2px solid var(--warn);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.asked-row__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.asked-row__title {
  min-width: 0;
  color: var(--text);
}
.asked-row__body {
  margin: 4px 0 0;
  white-space: pre-wrap;
}
.asked-row__acts {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
</style>
