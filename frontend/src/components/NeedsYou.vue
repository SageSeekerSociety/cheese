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
// 摆成一叠而不是一列：这一页钉在视口上、板在它下面按剩下的高度分列，所以这一块有
// 几条就占多高的话，板会被问题的条数挤扁——三条问题等于板少一行卡。一叠的高度和条
// 数无关，永远是一张卡：当下要答的那一条摆全，后面那几条只从底下露出一道边，说明
// 「后面还有」。答完一条下一条自己顶上来，板一动不动。
//
// 空的时候整块不出现：没人在等你，首页就不该多一块写着「暂无」的框。
import type { InboxItem } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import { getInbox, markRead, resolveAlert, sendFeedback } from '@/api'
import { label, NOTIF_KIND } from '@/labels'
import { myHandle } from '@/me'

const props = defineProps<{ projectId: string }>()

const rows = ref<InboxItem[]>([])
const actionError = ref('')
const busy = ref('')

/** 这一叠最多摆几张。第三张已经只剩一道边，再多一张看不出区别，只是多一层渲染。 */
const DEPTH = 3

/** 叠在最上面那一条是第几条。
 *
 *  存下标而不是 id，是因为「答完一条」和「翻到下一条」要的是同一个结果：一条被答
 *  掉之后从 `rows` 里消失，同一个下标指的就是紧接着那一条——不用另算该跳到哪儿。 */
const cursor = ref(0)

/** 这会儿摆出来的那几张，从 `cursor` 起往后数，到末尾绕回开头。
 *
 *  绕回去是因为「下一条」得能一直点下去：翻到最后一条还剩一个死掉的按钮，人会以为
 *  是坏的。只有一条的时候这一叠就是一张，底下不露边——那时候「后面还有」是假话。 */
const deck = computed(() => {
  const list = rows.value
  const n = Math.min(DEPTH, list.length)
  return Array.from({ length: n }, (_, i) => list[(cursor.value + i) % list.length])
})

/** 这一条给的选项。带选项的才答得了，其余只能读完收起来。 */
function optionsOf(row: InboxItem): string[] {
  const listed = row.payload?.options
  return Array.isArray(listed) ? listed.filter((option): option is string => typeof option === 'string') : []
}

function next() {
  if (rows.value.length > 1) cursor.value = (cursor.value + 1) % rows.value.length
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
    // 答掉的那一条不在了，队伍比刚才短：下标越界就回到第一条。别处不动 cursor——
    // 停在第几条是看的人翻到的位置，一次后台重读不该把他翻的进度抹掉。
    if (cursor.value >= rows.value.length) cursor.value = 0
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
    cursor.value = 0
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <!-- 最后一条被答掉的时候这一块收起来，而不是凭空消失：底下那块板会因此长高一张
       卡的高度，那一下得看得出是「这里空了」而不是「板自己跳了一下」。只收不展开
       ——这一块出现只在首次读到收件箱的那一刻，`Transition` 默认不做首帧动画。 -->
  <Transition name="asked-fold">
    <section v-if="rows.length" class="asked">
      <div class="asked__inner">
        <header class="asked__head">
          <h2 class="asked__title t-title">等你决定</h2>
          <!-- 「1/3」：一叠摆出来的是一条，所以件数得连着位置一起说，光写 3 会读成
               「这张卡有三个选项」。只有一条的时候不写——那时候位置不是信息。 -->
          <span v-if="rows.length > 1" class="asked__count t-meta c-faint">{{ cursor + 1 }}/{{ rows.length }}</span>
          <button v-if="rows.length > 1" type="button" class="asked__next t-meta" @click="next">
            下一条
            <v-icon size="14" aria-hidden="true">mdi-chevron-right</v-icon>
          </button>
        </header>
        <p v-if="actionError" role="alert" class="asked__error t-meta">{{ actionError }}</p>
        <!-- 一叠卡：三张都落在同一个网格格子里，所以这一叠的高度是最高那张的高度，
             和条数无关。错位靠 transform，而 transform 不占布局——翻一条的时候动的
             只有画面，格子不变，板因此一动不动。 -->
        <TransitionGroup tag="ul" name="asked-card" class="asked__deck">
          <li
            v-for="(row, depth) in deck"
            :key="row.id"
            class="asked-card"
            :class="`asked-card--d${depth}`"
            :aria-hidden="depth > 0 ? 'true' : undefined"
          >
            <!-- 后面那两张只露一道边，所以它们不装内容：装了也读不到，而按钮就算
                 看不见也还是能用 Tab 走进去、还是能点，那等于给同一个问题摆了三套
                 答案。 -->
            <template v-if="depth === 0">
              <div class="asked-card__head">
                <span class="asked-card__kind t-meta c-faint">{{ label(NOTIF_KIND, row.kind) }}</span>
                <span class="asked-card__title t-body">{{ row.title }}</span>
              </div>
              <!-- 两行，短的也占两行：一叠卡的高度必须是常数，否则答完一条、下一条
                   问得短一点，板就跟着抬一下。 -->
              <p class="asked-card__body t-meta c-muted">{{ row.body }}</p>
              <div class="asked-card__acts">
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
            </template>
          </li>
        </TransitionGroup>
      </div>
    </section>
  </Transition>
</template>

<style scoped>
/* 收起来那一下动的是 `grid-template-rows`：1fr → 0fr，里面那层 `overflow: hidden`
   把内容裁着跟上。高度写成 auto 的话没法过渡，写成一个具体像素值就得在这儿重算一
   遍卡有多高。 */
.asked {
  flex: 0 0 auto;
  display: grid;
  grid-template-rows: 1fr;
}
/* 内边距归到里面这一层：留在外面的话，这一块收到 0 之后还剩一条 14px 的空白。
   和「做出了什么」、下面那块板左右对齐——板自己 12px，这里再补 10px。 */
.asked__inner {
  min-height: 0;
  overflow: hidden;
  padding: 0 10px 14px;
}
.asked__head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 0 0 8px;
}
.asked__title {
  margin: 0;
}
.asked__count {
  font-variant-numeric: tabular-nums;
}
/* 悬停只改颜色，不改位置。 */
.asked__next {
  display: flex;
  align-items: center;
  margin-left: auto;
  color: var(--muted);
  cursor: pointer;
}
.asked__next:hover {
  color: var(--text);
}
/* 错误是给人读的一行字，所以用墨色那一档：`--danger` 是记号的颜色，写字读不出来。 */
.asked__error {
  margin: 0 0 8px;
  color: var(--danger-ink);
}
/* 三张卡共用一个格子。格子的高度是最高那张的高度，而最上面那张的结构（一行标题 +
   两行正文 + 一行按钮）是固定的，所以这一叠的高度是常数。 */
.asked__deck {
  display: grid;
  list-style: none;
  padding: 0;
  margin: 0;
}
/* 一条是一个问题：上面是它问了什么，下面是答它的那几个按钮。左边那道暖色竖线和
   看板里「待处理」那一列同一个 token——整个首页上「该你动手了」只有这一种颜色。 */
.asked-card {
  grid-area: 1 / 1;
  display: flex;
  flex-direction: column;
  padding: 10px 10px 6px 12px;
  border: 1px solid var(--line);
  border-left: 2px solid var(--warn);
  border-radius: var(--radius-md);
  background: var(--surface);
}
/* 后面那两张往下挪、缩一点、淡一点：露出来的那道边就是「后面还有」。它们不接事件
   ——点在那道边上要答的还是最上面那一条。 */
.asked-card--d0 {
  z-index: 3;
}
.asked-card--d1 {
  z-index: 2;
  transform: translateY(6px) scale(0.985);
  opacity: 0.6;
  pointer-events: none;
}
.asked-card--d2 {
  z-index: 1;
  transform: translateY(12px) scale(0.97);
  opacity: 0.35;
  pointer-events: none;
}
.asked-card__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
/* 标题一行，超出截断：两行的标题会把这一叠撑高一行，而它得是常数。 */
.asked-card__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.asked-card__body {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.8em;
  margin: 4px 0 0;
  line-height: 1.4;
  white-space: pre-wrap;
}
.asked-card__acts {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

/* 翻一条 / 答一条：留在叠里的那几张换了层，于是各自过渡到新的错位上——这是同一张
   卡往前顶，不是两张卡各自闪一下，所以动的是 transform 而不是显隐。
   不用在这儿关一遍减弱动效：全局那条 `prefers-reduced-motion: reduce` 把所有
   transition 压到 0.001ms，这一叠于是直接落在终态上，层次和错位照样读得出来。 */
.asked-card {
  transition:
    transform 0.3s ease,
    opacity 0.2s ease;
}
/* 答掉的那一条在原地淡出，后面那张同时顶上来。淡出期间它盖在最上面（不然它是在新
   的第一张后面消失的，看着像下一张先冒出来），也不再接事件。 */
.asked-card-leave-active {
  z-index: 4;
  pointer-events: none;
}
.asked-card-enter-from,
.asked-card-leave-to {
  opacity: 0;
}
.asked-fold-leave-active {
  transition:
    grid-template-rows 0.3s ease,
    opacity 0.2s ease;
}
.asked-fold-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
}
</style>
