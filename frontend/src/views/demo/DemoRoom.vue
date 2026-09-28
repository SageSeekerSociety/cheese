<script setup lang="ts">
// 演示里的那间房：对话栏是真的消息行（RoomMessage），右边「现场」是真的 PanelSite，
// 喂的是剧本算出来的数据（demoScene.frameAt）。首页 LandingRoom 是同一个做法。
// 外框（顶栏、机器、座位卡、页签）是演示自己画的：真页面上的这些要连后端才画得出来，
// 而演示要讲的恰恰是它们背后的机制，所以把机制写成看得见的几张卡。
import type { Block, Topic } from '@/cx_types'
import type { Frame, Scene } from './demoScene'

import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { answer } from './demoBackend'
import DemoBackstage from './DemoBackstage.vue'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import DispatchedMarker from '@/components/DispatchedMarker.vue'
import PanelSite from '@/components/panels/PanelSite.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import RoomNotice from '@/components/room/RoomNotice.vue'
import TimelineMark from '@/components/TimelineMark.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import { collapseNotices, type PlatformNotice } from '@/lib/platformNotice'

const props = defineProps<{ scene: Scene; frame: Frame }>()

const names = computed<Record<string, string>>(() =>
  Object.fromEntries(Object.entries(props.scene.people).map(([h, p]) => [h, p.name]))
)
const refs = computed(() => ({ mentionNames: names.value, topicTitles: {} }))
const isAgent = (handle: string) => props.scene.people[handle]?.agent === true
const defaultAgent = computed(() => props.scene.seats?.[0] ?? '')
const topic = { id: 'demo' } as Topic

// 时间线的行和产品里一样由 collapseNotices 算：藏掉不露面的、折叠同类事件、把同一轮
// 的动作行和改动摘要折成「本轮摘要」。分隔说明和已派出标记不是块，夹在中间原样放。
interface Row {
  key: string
  line: Frame['chat'][number]
  block: Block | null
  notice: PlatformNotice | null
  run: Block[]
}
const rows = computed<Row[]>(() => {
  const out: Row[] = []
  let pending: Frame['chat'] = []
  const flush = () => {
    if (!pending.length) return
    const byId = new Map(pending.map((l) => [l.block!.id, l]))
    for (const r of collapseNotices(pending.map((l) => l.block!))) {
      out.push({ key: r.block.id, line: byId.get(r.block.id)!, block: r.block, notice: r.notice, run: r.run })
    }
    pending = []
  }
  for (const line of props.frame.chat) {
    if (line.block) {
      pending.push(line)
      continue
    }
    flush()
    out.push({ key: line.id, line, block: null, notice: null, run: [] })
  }
  flush()
  return out
})

// 同一个人连着说，只有第一条带头像和名字；中间隔了一条分隔说明就重新带上。
function runStart(index: number): boolean {
  const row = rows.value[index]
  const prev = rows.value[index - 1]
  return !prev || !!prev.notice || !prev.block || prev.block.author !== row.block?.author
}

const working = computed(() => Object.keys(props.frame.running).length > 0)
const workingNames = computed(() => props.frame.runningWho.map((h) => names.value[h] ?? h).join('、'))

// ---- 把现场喂给真的 PanelSite ----
// PanelSite 平时从接口拉一页、再从 socket 一行行收（receive）。演示不连后端：
// 往前放时把新出来的几行 receive 进去；往回跳（或者哪一行变了样）就换一个新的
// PanelSite，从头喂一遍 —— 它手上的记录只增不减，这是让它回到过去的唯一办法。
const site = ref<InstanceType<typeof PanelSite> | null>(null)
const siteKey = ref(0)
let fed: Block[] = []

function feed(rows: Block[]): void {
  for (const b of rows) site.value?.receive(b)
}

watch(
  () => props.frame.site,
  async (rows) => {
    const extends_ = fed.length <= rows.length && fed.every((b, i) => rows[i].id === b.id)
    if (extends_) {
      feed(rows.slice(fed.length))
    } else {
      siteKey.value += 1
      await nextTick()
      feed(rows)
    }
    fed = rows
  }
)

onMounted(() => {
  fed = props.frame.site
  feed(fed)
})

// 验收卡是真的 TopicAcceptCard，它自己去取数：剧本里的卡和检查交给演示后端
// 回答，卡一变就换一张新的，让它重新取一次（它自己十五秒才刷一回）。
const cardKey = computed(() => JSON.stringify([props.frame.card, props.frame.checks, props.frame.cardOpen]))
const cardBox = ref<HTMLElement | null>(null)

// 卡面默认是收着的一行；剧本说要摊开，就替人点一下卡上的展开钮。卡自己去取数，
// 所以钮要等它取回来才出现 —— 最多等一秒。
async function openCard(): Promise<void> {
  for (let i = 0; i < 20 && props.frame.cardOpen; i++) {
    const toggle = cardBox.value?.querySelector<HTMLElement>('[aria-expanded="false"]')
    if (toggle) return toggle.click()
    await new Promise((r) => setTimeout(r, 50))
  }
}
watch(cardKey, () => void nextTick(openCard), { immediate: true })
watch(
  cardKey,
  () => {
    const card = props.frame.card
    const checks = props.frame.checks
    answer('/topics/demo/accept-card', card ? () => ({ data: [card], total: 1 }) : null)
    answer('/topics/demo/pr-checks', () => checks ?? { available: false })
  },
  { immediate: true }
)

const TABS = ['总览', '现场', '改动', '预览']
</script>

<template>
  <div class="demo-room" :data-focus="frame.focus ?? ''" inert>
    <header class="demo-bar">
      <span class="demo-bar-project">{{ scene.project }}</span>
      <Transition name="demo-swap" mode="out-in">
        <span :key="frame.topic" class="demo-bar-topic" data-region="title"># {{ frame.topic }}</span>
      </Transition>
      <span class="demo-bar-machine" data-region="machine">
        <v-icon icon="mdi-server" size="14" />
        <Transition name="demo-swap" mode="out-in">
          <span :key="frame.machine">{{ frame.machine }}</span>
        </Transition>
      </span>
    </header>

    <div v-if="frame.seats.length" class="demo-seats" data-region="seats">
      <div
        v-for="s in frame.seats"
        :key="s.who"
        class="demo-seat"
        :class="{ 'demo-seat-busy': frame.runningWho.includes(s.who) }"
      >
        <CheeseAvatar :size="22" :name="names[s.who] ?? s.who" />
        <div class="demo-seat-body">
          <div class="demo-seat-name">
            {{ names[s.who] ?? s.who }}
            <span v-if="s.who === defaultAgent && frame.seats.length > 1" class="demo-seat-tag">默认</span>
            <span v-if="frame.lock === s.who" class="demo-seat-lock">
              <v-icon icon="mdi-lock-outline" size="12" />
              重资源锁
            </span>
          </div>
          <div class="demo-seat-meta">
            <span v-if="s.dir">{{ s.dir }}</span>
            <span v-if="s.session">{{ s.session }}</span>
            <span v-if="s.state" class="demo-seat-state">{{ s.state }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="demo-body">
      <div class="demo-chat" data-region="chat">
        <TransitionGroup name="demo-line" tag="div" class="demo-lines">
          <template v-for="(row, i) in rows" :key="row.key">
            <TimelineMark v-if="row.line.kind === 'mark'" quiet>{{ row.line.text }}</TimelineMark>
            <DispatchedMarker v-else-if="row.line.kind === 'split' && row.line.split" :marker="row.line.split" />
            <RoomNotice
              v-else-if="row.block && row.notice"
              :block="row.block"
              :notice="row.notice"
              :run="row.run"
              :name="row.block.author === 'system' ? null : names[row.block.author] ?? null"
              :time="row.line.time"
              :agent-name="names[defaultAgent] ?? '芝士'"
              :refs="refs"
            />
            <RoomMessage
              v-else-if="row.block"
              :block="row.block"
              :parent="null"
              :parent-name="null"
              :run-start="runStart(i)"
              :mine="false"
              :topic-id="null"
              :author-name="names[row.block.author] ?? row.block.author"
              :avatar="null"
              :is-agent="isAgent(row.block.author)"
              :time="row.line.time"
              :refs="refs"
              viewer=""
              :ask-busy="false"
            />
          </template>
        </TransitionGroup>
        <div v-if="frame.card" ref="cardBox" class="demo-card" data-region="card">
          <TopicAcceptCard :key="cardKey" topic-id="demo" topic-status="active" :task-id="frame.card.task_id" docked />
        </div>
        <div class="demo-composer">发消息，@ 队友让它干活</div>
      </div>

      <aside class="demo-panel">
        <nav class="demo-tabs" data-region="tabs">
          <span v-for="tab in TABS" :key="tab" class="demo-tab" :class="{ 'demo-tab-on': tab === '现场' }">
            {{ tab }}
            <template v-if="tab === '现场' && working">
              <i class="demo-tab-pulse" />
              <small class="demo-tab-who">{{ workingNames }}正在工作</small>
            </template>
          </span>
        </nav>
        <div class="demo-site" data-region="site">
          <PanelSite
            :key="siteKey"
            ref="site"
            :topic="topic"
            :active="false"
            :member-names="names"
            :working="working"
            :running-turns="frame.running"
          />
        </div>
        <DemoBackstage v-if="scene.backstage" :scene="scene" :frame="frame" class="demo-backstage" />
      </aside>
    </div>

    <Transition name="demo-swap">
      <p v-if="frame.tag" :key="frame.step" class="demo-callout" :data-for="frame.focus ?? ''">{{ frame.tag }}</p>
    </Transition>
  </div>
</template>

<style scoped>
.demo-room {
  display: flex;
  position: relative;
  height: 100%;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  flex-direction: column;
}

.demo-bar {
  display: flex;
  flex: none;
  gap: 12px;
  align-items: center;
  height: 44px;
  padding: 0 16px;
  font-size: 14px;
  line-height: var(--lh-14);
  border-bottom: 1px solid var(--line);
}

.demo-bar-project {
  font-weight: 600;
  color: var(--ink);
}

.demo-bar-topic {
  padding: 2px 6px;
  color: var(--muted);
  border-radius: var(--radius-sm);
}

.demo-bar-machine {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 2px 8px;
  margin-left: auto;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.demo-seats {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 16px;
  background: var(--fill);
  border-bottom: 1px solid var(--line);
}

.demo-seat {
  display: flex;
  flex: 1 1 200px;
  gap: 8px;
  align-items: center;
  min-width: 0;
  padding: 6px 10px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-base) var(--ease-standard);
}

.demo-seat-busy {
  border-color: var(--accent);
}

.demo-seat-body {
  min-width: 0;
}

.demo-seat-name {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--ink);
}

.demo-seat-tag,
.demo-seat-lock {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  padding: 0 6px;
  font-size: 11px;
  font-weight: 400;
  color: var(--muted);
  background: var(--fill-2);
  border-radius: var(--radius-sm);
}

.demo-seat-lock {
  color: var(--ink);
}

.demo-seat-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0 10px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.demo-seat-state {
  font-family: inherit;
  color: var(--muted);
}

.demo-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.demo-chat {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  padding: 12px 0;
}

.demo-lines {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: flex-end;
  overflow: hidden;
}

.demo-card {
  margin: 8px 16px 0;
}

.demo-composer {
  display: flex;
  align-items: center;
  height: 40px;
  padding: 0 12px;
  margin: 10px 16px 0;
  font-size: 13px;
  color: var(--faint);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.demo-panel {
  display: flex;
  flex: none;
  flex-direction: column;
  width: 48%;
  min-width: 0;
  border-left: 1px solid var(--line);
}

.demo-tabs {
  display: flex;
  flex: none;
  gap: 4px;
  align-items: center;
  height: 40px;
  padding: 0 8px;
  border-bottom: 1px solid var(--line);
}

.demo-tab {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 4px 10px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  border-radius: var(--radius-sm);
}

.demo-tab-on {
  color: var(--ink);
  background: var(--fill);
}

.demo-tab-pulse {
  width: 6px;
  height: 6px;
  background: var(--accent);
  border-radius: 50%;
  animation: demo-pulse 1.2s ease-in-out infinite;
}

.demo-tab-who {
  font-size: 11px;
  color: var(--muted);
}

.demo-backstage {
  flex: 1.2;
}

.demo-site {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.demo-site :deep(.panel-site) {
  height: 100%;
}

/* 会话详情那一条要连后端才有内容，演示里不摆一条「没有会话」出来。 */
.demo-site :deep(.session-inspector) {
  display: none;
}

/* 这一步该看哪一块：描一圈边。 */
[data-region] {
  transition: box-shadow var(--dur-base) var(--ease-standard);
}

.demo-room[data-focus='machine'] [data-region='machine'],
.demo-room[data-focus='seats'] [data-region='seats'],
.demo-room[data-focus='chat'] [data-region='chat'],
.demo-room[data-focus='site'] [data-region='site'],
.demo-room[data-focus='tabs'] [data-region='tabs'],
.demo-room[data-focus='title'] [data-region='title'],
.demo-room[data-focus='backstage'] [data-region='backstage'],
.demo-room[data-focus='card'] [data-region='card'] {
  box-shadow: inset 0 0 0 2px var(--accent);
}

.demo-callout {
  position: absolute;
  right: 16px;
  bottom: 16px;
  max-width: 60%;
  padding: 6px 12px;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--surface);
  background: var(--ink);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
}

.demo-line-enter-active,
.demo-swap-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.demo-swap-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.demo-line-enter-from,
.demo-swap-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.demo-swap-leave-to {
  opacity: 0;
}

@keyframes demo-pulse {
  50% {
    opacity: 0.3;
  }
}

@media (prefers-reduced-motion: reduce) {
  .demo-line-enter-active,
  .demo-swap-enter-active,
  .demo-swap-leave-active {
    transition: none;
  }

  .demo-tab-pulse {
    animation: none;
  }
}
</style>
