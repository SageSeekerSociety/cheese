<script setup lang="ts">
// 演示里的那间房：对话栏是真的消息行（RoomMessage），右边「现场」是真的 PanelSite，
// 喂的是剧本算出来的数据（demoScene.frameAt）。首页 LandingRoom 是同一个做法。
// 外框（顶栏、机器、座位卡、页签）是演示自己画的：真页面上的这些要连后端才画得出来，
// 而演示要讲的恰恰是它们背后的机制，所以把机制写成看得见的几张卡。
import type { Frame, Scene } from './demoScene'
import type { Block, Topic } from '@/cx_types'

import { computed, nextTick, onMounted, ref, watch } from 'vue'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import PanelSite from '@/components/panels/PanelSite.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import TimelineMark from '@/components/TimelineMark.vue'

const props = defineProps<{ scene: Scene; frame: Frame }>()

const names = computed<Record<string, string>>(() =>
  Object.fromEntries(Object.entries(props.scene.people).map(([h, p]) => [h, p.name]))
)
const refs = computed(() => ({ mentionNames: names.value, topicTitles: {} }))
const isAgent = (handle: string) => props.scene.people[handle]?.agent === true
const defaultAgent = computed(() => props.scene.seats?.[0] ?? '')
const topic = { id: 'demo' } as Topic

function block(line: Frame['chat'][number]): Block {
  return {
    id: line.id,
    topic_id: 'demo',
    kind: 'message',
    author_type: 'participant',
    author: line.author,
    content: line.text,
    created_at: '',
  }
}

// 同一个人连着说，只有第一条带头像和名字；中间隔了一条分隔说明就重新带上。
function runStart(index: number): boolean {
  const line = props.frame.chat[index]
  const prev = props.frame.chat[index - 1]
  return !prev || prev.kind !== 'message' || prev.author !== line.author
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

const TABS = ['总览', '现场', '改动', '预览']
</script>

<template>
  <div class="demo-room" :data-focus="frame.focus ?? ''" inert>
    <header class="demo-bar">
      <span class="demo-bar__project">{{ scene.project }}</span>
      <Transition name="demo-swap" mode="out-in">
        <span :key="frame.topic" class="demo-bar__topic" data-region="title"># {{ frame.topic }}</span>
      </Transition>
      <span class="demo-bar__machine" data-region="machine">
        <v-icon icon="mdi-server" size="14" />
        <Transition name="demo-swap" mode="out-in">
          <span :key="frame.machine">{{ frame.machine }}</span>
        </Transition>
      </span>
    </header>

    <div v-if="frame.seats.length" class="demo-seats" data-region="seats">
      <div v-for="s in frame.seats" :key="s.who" class="demo-seat" :class="{ 'demo-seat--busy': frame.runningWho.includes(s.who) }">
        <CheeseAvatar :size="22" :name="names[s.who] ?? s.who" />
        <div class="demo-seat__body">
          <div class="demo-seat__name">
            {{ names[s.who] ?? s.who }}
            <span v-if="s.who === defaultAgent && frame.seats.length > 1" class="demo-seat__tag">默认</span>
            <span v-if="frame.lock === s.who" class="demo-seat__lock">
              <v-icon icon="mdi-lock-outline" size="12" />
              重资源锁
            </span>
          </div>
          <div class="demo-seat__meta">
            <span v-if="s.dir">{{ s.dir }}</span>
            <span v-if="s.session">{{ s.session }}</span>
            <span v-if="s.state" class="demo-seat__state">{{ s.state }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="demo-body">
      <div class="demo-chat" data-region="chat">
        <TransitionGroup name="demo-line" tag="div" class="demo-lines">
          <template v-for="(line, i) in frame.chat" :key="line.id">
            <TimelineMark v-if="line.kind === 'mark'" quiet>{{ line.text }}</TimelineMark>
            <RoomMessage
              v-else
              :block="block(line)"
              :parent="null"
              :parent-name="null"
              :run-start="runStart(i)"
              :mine="false"
              :topic-id="null"
              :author-name="names[line.author] ?? line.author"
              :avatar="null"
              :is-agent="isAgent(line.author)"
              :time="line.time"
              :refs="refs"
              viewer=""
              :ask-busy="false"
            />
          </template>
        </TransitionGroup>
        <div class="demo-composer">发消息，@ 队友让它干活</div>
      </div>

      <aside class="demo-panel">
        <nav class="demo-tabs" data-region="tabs">
          <span v-for="tab in TABS" :key="tab" class="demo-tab" :class="{ 'demo-tab--on': tab === '现场' }">
            {{ tab }}
            <template v-if="tab === '现场' && working">
              <i class="demo-tab__pulse" />
              <small class="demo-tab__who">{{ workingNames }}正在工作</small>
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
      </aside>
    </div>

    <Transition name="demo-swap">
      <p v-if="frame.tag" :key="frame.step" class="demo-callout" :data-for="frame.focus ?? ''">{{ frame.tag }}</p>
    </Transition>
  </div>
</template>

<style scoped>
.demo-room {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
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

.demo-bar__project {
  font-weight: 600;
  color: var(--ink);
}

.demo-bar__topic {
  padding: 2px 6px;
  color: var(--muted);
  border-radius: var(--radius-sm);
}

.demo-bar__machine {
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

.demo-seat--busy {
  border-color: var(--primary);
}

.demo-seat__body {
  min-width: 0;
}

.demo-seat__name {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--ink);
}

.demo-seat__tag,
.demo-seat__lock {
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

.demo-seat__lock {
  color: var(--ink);
}

.demo-seat__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0 10px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.demo-seat__state {
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

.demo-tab--on {
  color: var(--ink);
  background: var(--fill);
}

.demo-tab__pulse {
  width: 6px;
  height: 6px;
  background: var(--primary);
  border-radius: 50%;
  animation: demo-pulse 1.2s ease-in-out infinite;
}

.demo-tab__who {
  font-size: 11px;
  color: var(--muted);
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
.demo-room[data-focus='title'] [data-region='title'] {
  box-shadow: inset 0 0 0 2px var(--primary);
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
  color: var(--on-primary, #fff);
  background: var(--primary);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2, 0 4px 16px rgb(0 0 0 / 15%));
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

  .demo-tab__pulse {
    animation: none;
  }
}
</style>
