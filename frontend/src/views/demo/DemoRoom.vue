<script setup lang="ts">
// 演示里的那间房：对话栏是真的消息行（RoomMessage），右边是工作面板——页签条
// （PanelTabs）加当前那一格的产品组件（总览 / 现场 / 改动 / 预览），喂的是剧本算
// 出来的数据（demoScene.frameAt + demoPanels）。首页 LandingRoom 是同一个做法。
// 外框（顶栏、机器、座位卡）是演示自己画的：真页面上的这些要连后端才画得出来，
// 而演示要讲的恰恰是它们背后的机制，所以把机制写成看得见的几张卡。
import type { PanelTab } from '@/components/panels/PanelTabs.vue'
import type { Block, Topic } from '@/cx_types'
import type { TaskLine } from '@/lib/channelTasks'
import type { Frame, PanelKey, Scene, SplitLine } from './demoScene'

import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { answer } from './demoBackend'
import DemoBackstage from './DemoBackstage.vue'
import { DEMO_PROJECT, DEMO_TOPIC, demoTaskId, installPanelAnswers } from './demoPanels'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import { panelTabs } from '@/components/panels/panelTabList'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import RoomNotice from '@/components/room/RoomNotice.vue'
import TaskCard from '@/components/room/TaskCard.vue'
import TimelineMark from '@/components/TimelineMark.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import PanelChangesHost from '@/components/work/PanelChangesHost.vue'
import PanelDocHost from '@/components/work/PanelDocHost.vue'
import PanelPreviewHost from '@/components/work/PanelPreviewHost.vue'
import PanelSiteHost from '@/components/work/PanelSiteHost.vue'
import { t } from '@/i18n'
import { collapseNotices, type PlatformNotice } from '@/lib/platformNotice'

const props = defineProps<{ scene: Scene; frame: Frame }>()

const names = computed<Record<string, string>>(() =>
  Object.fromEntries(Object.entries(props.scene.people).map(([h, p]) => [h, p.name]))
)
const refs = computed(() => ({ mentionNames: names.value, topicTitles: {} }))
const isAgent = (handle: string) => props.scene.people[handle]?.agent === true
const defaultAgent = computed(() => props.scene.seats?.[0] ?? '')
// 和产品里同一个房间：房间 id 就是演示后端回答的那一个，标题跟着剧本走。
const topic = computed<Topic>(
  () =>
    ({
      id: DEMO_TOPIC,
      project_id: DEMO_PROJECT,
      title: props.scene.topic,
      status: 'active',
    }) as Topic
)

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
// PanelSite（经它的接线外壳 PanelSiteHost）平时从接口拉一页、再从 socket 一行行收
// （receive）。演示不连后端：往前放时把新出来的几行 receive 进去；往回跳（或者哪一行
// 变了样）就换一个新的，从头喂一遍 —— 它手上的记录只增不减，这是让它回到过去的唯一办法。
const site = ref<InstanceType<typeof PanelSiteHost> | null>(null)
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

// ---- 右侧那几格：页签条是产品的那条，格子里是产品的那四件 ----
// 哪几格、什么名字、挂哪个图标，来自产品那张表（panelTabs）——桌面上的工作面板就是
// 这四格（手机上对话自己是一格，这里对话在左边那一栏）。这一刻停在哪一格由剧本说
// （`frame.panel`，不写就是现场），格子的内容也是剧本给的，画法是产品自己的。
const PANEL_TABS = panelTabs(false)

// 剧本里出现过内容的格子。一次都没出现过的格子字退淡一点——产品里这一格没东西就是
// 这个样子（那是「还没有改动」，不是「没有这个功能」）。
const filled = computed(() => {
  const set = new Set<PanelKey>(['site'])
  for (const s of props.scene.steps) {
    if (s.overview !== undefined) set.add('overview')
    if (s.changes !== undefined) set.add('changes')
    if (s.preview !== undefined) set.add('preview')
  }
  return set
})

function signalOf(key: PanelKey): PanelTab['signal'] {
  // 和产品同一套说法：现场是「正在发生」的呼吸点，改动是数字，预览是新内容。
  if (key === 'site' && working.value) return { kind: 'pulse' }
  if (key === 'changes' && props.frame.changes) return { kind: 'count', count: props.frame.changes.files.length }
  if (key === 'preview' && props.frame.preview) return { kind: 'dot' }
  if (key === 'overview' && props.frame.overview?.tasks?.length)
    return { kind: 'count', count: props.frame.overview.tasks.length }
  return undefined
}

/** hover / 读屏的说法，和产品里那几行一个意思。 */
function tabTitle(key: PanelKey): string {
  const label = PANEL_TABS.find((t) => t.key === key)?.label ?? key
  if (key === 'site' && working.value) return `${label}（${workingNames.value}正在工作）`
  if (key === 'changes' && props.frame.changes) return `${label}（${props.frame.changes.files.length} 个文件改了）`
  if (key === 'preview' && props.frame.preview) return `${label}（${props.frame.preview.path}）`
  if (key === 'overview' && props.frame.overview?.tasks?.length)
    return `${label}（${props.frame.overview.tasks.length} 件任务）`
  return label
}

const panelTabList = computed<PanelTab[]>(() =>
  PANEL_TABS.map((tab) => {
    const key = tab.key as PanelKey
    return {
      key: tab.key,
      label: tab.label,
      icon: tab.icon,
      empty: !filled.value.has(key),
      title: tabTitle(key),
      signal: signalOf(key),
    }
  })
)

// 哪几格已经挂上过。产品里也是这样：一格第一次被看到才挂，之后一直挂着（切走是
// `v-show` 藏起来）。挂上再卸掉会丢掉它取回来的东西和滚动位置，还会重新取一次数；
// 现场更是要一直挂着——它是重放喂进去的，卸掉再挂就断了。
const mounted = ref<Set<PanelKey>>(new Set<PanelKey>(['site']))
watch(
  () => props.frame.panel,
  (key) => {
    if (!mounted.value.has(key)) mounted.value = new Set([...mounted.value, key])
  },
  { immediate: true }
)

// 这几格自己去接口取数，演示页没有后端：每一帧它们该读到什么，在这里装到演示后端
// 上（形状见 demoPanels）。盯的是剧本声明的那几样，不是整帧——帧每一动画帧都是新
// 的，而这几样是剧本里的对象，剧本不动它们就不变，装一次就够。
watch([() => props.frame.overview, () => props.frame.changes, () => props.frame.preview], () =>
  installPanelAnswers(props.frame)
)
installPanelAnswers(props.frame)

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
    // The card is read through its task's own conversation.
    const conversation = card?.task_id ?? 'demo'
    answer(`/topics/${conversation}/accept-card`, card ? () => ({ data: [card], total: 1 }) : null)
    answer(`/topics/${conversation}/pr-checks`, () => checks ?? { available: false })
  },
  { immediate: true }
)

// 演示里的任务只有在做和做完两档，画成频道里同一种任务卡。
function splitTask(split: SplitLine): TaskLine {
  return {
    id: split.taskId,
    title: split.title,
    owner: null,
    creator: null,
    status: split.done ? t('work.board.phrase.accepted') : t('work.board.phrase.running'),
    tone: split.done ? 'done' : 'running',
    accepted: null,
    at: '',
  }
}
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
        <CheeseAvatar :size="22" :name="names[s.who] ?? s.who" :handle="s.who" />
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
            <TaskCard
              v-else-if="row.line.kind === 'split' && row.line.split"
              class="demo-task"
              :task="splitTask(row.line.split)"
              :owner-name="null"
            />
            <RoomNotice
              v-else-if="row.block && row.notice"
              :block="row.block"
              :notice="row.notice"
              :run="row.run"
              :agent="
                row.block.author === 'system' || !names[row.block.author]
                  ? null
                  : { name: names[row.block.author]!, handle: row.block.author }
              "
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
        <PanelTabs data-region="tabs" :tabs="panelTabList" :active="frame.panel" />
        <!-- 当前那一格。四格都在这里，切走的是藏起来的那几格（和产品一样），
             它们的接口调用由各格自己在「轮到我上场」那一下发起。 -->
        <div class="demo-tabbody" data-region="panel">
          <PanelDocHost
            v-if="mounted.has('overview')"
            v-show="frame.panel === 'overview'"
            :topic="topic"
            :task-id="DEMO_TOPIC"
            :activity-tick="frame.step"
          />
          <PanelSiteHost
            v-if="mounted.has('site')"
            v-show="frame.panel === 'site'"
            :key="siteKey"
            ref="site"
            :topic-id="topic.id"
            :project-id="DEMO_PROJECT"
            :active="false"
            :member-names="names"
            :working="working"
            :running-turns="frame.running"
          />
          <PanelChangesHost
            v-if="mounted.has('changes')"
            v-show="frame.panel === 'changes'"
            :topic-id="DEMO_TOPIC"
            :task-id="demoTaskId(frame.overview?.tasks)"
            :project-id="DEMO_PROJECT"
            :active="frame.panel === 'changes'"
          />
          <PanelPreviewHost
            v-if="mounted.has('preview')"
            v-show="frame.panel === 'preview'"
            :topic-id="DEMO_TOPIC"
            :project-id="DEMO_PROJECT"
            :active="frame.panel === 'preview'"
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

.demo-backstage {
  flex: 1.2;
}

/* 当前那一格。四格都挂在这一个盒子里，各自 `flex: 1 1 auto`（见各面板自己的根
   元素），所以它只负责给出高度。 */
.demo-tabbody {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}

/* 会话详情那一条要连后端才有内容，演示里不摆一条「没有会话」出来。 */
.demo-tabbody :deep(.session-inspector) {
  display: none;
}

/* 这一步该看哪一块：描一圈边。 */
[data-region] {
  transition: box-shadow var(--dur-base) var(--ease-standard);
}

.demo-room[data-focus='machine'] [data-region='machine'],
.demo-room[data-focus='seats'] [data-region='seats'],
.demo-room[data-focus='chat'] [data-region='chat'],
.demo-room[data-focus='site'] [data-region='panel'],
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

@media (prefers-reduced-motion: reduce) {
  .demo-line-enter-active,
  .demo-swap-enter-active,
  .demo-swap-leave-active {
    transition: none;
  }
}
</style>
