<script setup lang="ts">
// 「幕后」那一格：界面上看不见、但这一段要讲的机制。和上面那格真的现场并排，
// 让读的人同时看见「房间里发生了什么」和「平台底下在干什么」。
// 三种画法：记忆文件树、一次请求经过的几站、设备和连接器。
import type { DeviceStatus, Frame, Scene } from './demoScene'

import { computed } from 'vue'

const props = defineProps<{ scene: Scene; frame: Frame }>()

const TITLES = { memory: '幕后 · 记忆文件', pipeline: '幕后 · 这次请求', devices: '幕后 · 机器' } as const
const title = computed(() => (props.scene.backstage ? TITLES[props.scene.backstage] : ''))

// ---- memory ----
// 按目录分组：team/、private/<handle>/ 各一组，组里 MEMORY.md 排第一。
const folders = computed(() => {
  const groups = new Map<string, Frame['files']>()
  for (const f of props.frame.files) {
    const cut = f.path.lastIndexOf('/')
    const dir = f.path.slice(0, cut + 1)
    const list = groups.get(dir) ?? []
    list.push(f)
    groups.set(dir, list)
  }
  return [...groups].map(([dir, list]) => ({
    dir,
    files: list.sort((a, b) => (a.path.endsWith('MEMORY.md') ? -1 : b.path.endsWith('MEMORY.md') ? 1 : 0)),
  }))
})
const base = (path: string) => path.slice(path.lastIndexOf('/') + 1)

// ---- devices ----
const STATUS: Record<DeviceStatus, string> = {
  offline: '离线',
  pairing: '配对中',
  online: '在线',
  busy: '在跑一轮',
  lost: '连不上',
  cooling: '隔离冷却中',
}
</script>

<template>
  <section class="stage" data-region="backstage" :aria-label="title">
    <header class="stage-head">
      <span>{{ title }}</span>
      <span v-for="m in frame.meters" :key="m.label" class="stage-meter">
        {{ m.label }} <b>{{ m.value }}</b>
      </span>
    </header>

    <div v-if="scene.backstage === 'memory'" class="stage-body mem">
      <div v-for="g in folders" :key="g.dir" class="mem-dir">
        <p class="mem-dir-name">{{ g.dir }}</p>
        <TransitionGroup name="stage-in" tag="div">
          <div
            v-for="f in g.files"
            :key="f.path"
            class="mem-file"
            :class="{ 'mem-file-in': frame.injected.includes(f.path), 'mem-file-fresh': f.fresh }"
          >
            <div class="mem-file-name">
              <v-icon
                :icon="f.path.endsWith('MEMORY.md') ? 'mdi-format-list-bulleted' : 'mdi-file-document-outline'"
                size="13"
              />
              {{ base(f.path) }}
              <span v-if="frame.injected.includes(f.path)" class="mem-tag mem-tag-in">装进这一轮</span>
              <span v-else-if="f.fresh" class="mem-tag">刚改</span>
            </div>
            <TransitionGroup name="stage-in" tag="ul" class="mem-lines">
              <li v-for="line in f.lines" :key="line">{{ line }}</li>
            </TransitionGroup>
          </div>
        </TransitionGroup>
      </div>
    </div>

    <ol v-else-if="scene.backstage === 'pipeline'" class="stage-body pipe">
      <li
        v-for="st in scene.stations"
        :key="st.id"
        class="pipe-st"
        :class="[`pipe-st-${frame.stations[st.id]?.state ?? 'off'}`, { 'pipe-st-at': frame.at === st.id }]"
      >
        <i class="pipe-dot" />
        <div class="pipe-body">
          <b>{{ st.label }}</b>
          <Transition name="stage-in" mode="out-in">
            <span :key="frame.stations[st.id]?.note || st.note" class="pipe-note">
              {{ frame.stations[st.id]?.note || st.note }}
            </span>
          </Transition>
        </div>
      </li>
    </ol>

    <div v-else-if="scene.backstage === 'devices'" class="stage-body dev">
      <div class="dev-cards">
        <div
          v-for="d in scene.devices"
          :key="d.id"
          class="dev-card"
          :class="`dev-card-${frame.devices[d.id]?.status ?? 'offline'}`"
        >
          <v-icon :icon="d.kind === '云机器' ? 'mdi-cloud-outline' : 'mdi-laptop'" size="18" />
          <div class="dev-card-body">
            <b class="dev-card-name">{{ d.name }}</b>
            <span>{{ d.kind }} · {{ STATUS[frame.devices[d.id]?.status ?? 'offline'] }}</span>
            <small v-if="frame.devices[d.id]?.text">{{ frame.devices[d.id].text }}</small>
          </div>
          <i class="dev-dot" />
        </div>
      </div>
      <TransitionGroup name="stage-in" tag="pre" class="dev-term">
        <span v-for="(line, i) in frame.term.slice(-7)" :key="frame.term.length - 7 + i">{{ line }}</span>
      </TransitionGroup>
    </div>
  </section>
</template>

<style scoped>
.stage {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--fill);
  border-top: 1px solid var(--line);
}

.stage-head {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  padding: 8px 12px 4px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.stage-meter {
  padding: 0 6px;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}

.stage-body {
  flex: 1;
  min-height: 0;
  padding: 4px 12px 12px;
  margin: 0;
  overflow: hidden;
}

/* ---- memory ---- */
.mem {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.mem-dir-name {
  margin: 0 0 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--faint);
}

.mem-file {
  padding: 6px 8px;
  margin-bottom: 6px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  transition:
    border-color var(--dur-base) var(--ease-standard),
    box-shadow var(--dur-base) var(--ease-standard);
}

.mem-file-in {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-wash);
}

.mem-file-fresh {
  border-color: var(--ok);
}

.mem-file-name {
  display: flex;
  gap: 4px;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink);
}

.mem-tag {
  padding: 0 4px;
  margin-left: auto;
  font-family: inherit;
  font-size: 10px;
  color: var(--ok-ink);
  background: var(--ok-wash);
  border-radius: var(--radius-sm);
}

.mem-tag-in {
  color: var(--accent-ink);
  background: var(--accent-wash);
}

.mem-lines {
  padding: 0;
  margin: 4px 0 0;
  list-style: none;
}

.mem-lines li {
  overflow: hidden;
  font-size: 11px;
  line-height: var(--lh-12);
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- pipeline ---- */
.pipe {
  display: flex;
  flex-direction: column;
  gap: 2px;
  list-style: none;
}

.pipe-st {
  display: flex;
  position: relative;
  padding: 4px 6px;
  border-radius: var(--radius-sm);
  gap: 10px;
  transition: background-color var(--dur-base) var(--ease-standard);
}

.pipe-st:not(:last-child)::after {
  position: absolute;
  top: 18px;
  bottom: -8px;
  left: 11px;
  width: 2px;
  background: var(--line-2);
  content: '';
}

.pipe-dot {
  z-index: var(--z-raised);
  flex: none;
  width: 12px;
  height: 12px;
  margin-top: 3px;
  background: var(--surface);
  border: 2px solid var(--line-2);
  border-radius: 50%;
  transition:
    background-color var(--dur-base) var(--ease-standard),
    border-color var(--dur-base) var(--ease-standard);
}

.pipe-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  font-size: 12px;
  line-height: var(--lh-12);
}

.pipe-body b {
  font-weight: 600;
  color: var(--ink);
}

.pipe-note {
  color: var(--muted);
}

.pipe-st-off .pipe-body b {
  color: var(--faint);
}

.pipe-st-on .pipe-dot {
  background: var(--accent);
  border-color: var(--accent);
  animation: stage-pulse 1s ease-in-out infinite;
}

.pipe-st-ok .pipe-dot {
  background: var(--ok);
  border-color: var(--ok);
}

.pipe-st-deny .pipe-dot {
  background: var(--danger);
  border-color: var(--danger);
}

.pipe-st-deny .pipe-note {
  color: var(--danger-ink);
}

.pipe-st-at {
  background: var(--surface);
}

/* ---- devices ---- */
.dev {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dev-cards {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.dev-card {
  display: flex;
  flex: 1 1 180px;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  color: var(--muted);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.dev-card-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  font-size: 12px;
  line-height: var(--lh-12);
}

.dev-card-name {
  font-size: 13px;
  color: var(--ink);
}

.dev-card-body small {
  color: var(--faint);
}

.dev-dot {
  flex: none;
  width: 8px;
  height: 8px;
  background: var(--line-2);
  border-radius: 50%;
}

.dev-card-pairing .dev-dot {
  background: var(--warn);
}

.dev-card-online .dev-dot {
  background: var(--ok);
}

.dev-card-busy {
  border-color: var(--accent);
}

.dev-card-busy .dev-dot {
  background: var(--accent);
  animation: stage-pulse 1s ease-in-out infinite;
}

.dev-card-lost .dev-dot,
.dev-card-cooling .dev-dot {
  background: var(--danger);
}

.dev-card-lost,
.dev-card-cooling {
  border-color: var(--danger);
}

.dev-term {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 8px 10px;
  margin: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre-wrap;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.stage-in-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.stage-in-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

@keyframes stage-pulse {
  50% {
    opacity: 0.4;
  }
}

@media (prefers-reduced-motion: reduce) {
  .stage-in-enter-active {
    transition: none;
  }

  .pipe-st-on .pipe-dot,
  .dev-card-busy .dev-dot {
    animation: none;
  }
}
</style>
