<script setup lang="ts">
// A channel's 文档, like a canvas pinned to the channel. It is also a project
// file (docs/频道/<频道名>.md), and 项目文件 opens this same component. Three parts:
//
//   这个频道的事  a live block: every 一件事 opened in the channel (讨论 and
//                任务) read from S.things, so a 讨论 opened a moment ago is
//                already here. A row opens it in the right panel.
//   芝士整理     芝士's own block: what was decided, found, and is still open,
//                each sentence naming who said it, ↗ showing that message in
//                对话, and an open question one click from a 一件事.
//   大家写的     what people wrote, in the product's document editor; a
//                person's edit applies at once.
//
// The rule between the last two: 芝士 updates only its own block directly. It
// never rewrites what people wrote; a change it wants there is a pending
// suggestion that a person accepts, as everywhere else in the project.
import type { SummaryKind } from './data'

import { computed, ref } from 'vue'

import { channelOf, NAMES, projectOfChan, SUMMARY, SUMMARY_LABELS } from './data'
import DemoDoc from './DemoDoc.vue'
import { hhmm, stateOf, statusText, whenText } from './shapes'
import { editChanDoc, jumpTo, openThing, S, startFromDoc } from './state'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import { relTime } from '@/lib/relTime'

const props = defineProps<{ chan: string }>()

const channel = computed(() => channelOf(props.chan)!)
/** 一件事 exist only where work is handed to 芝士: not in the course or the projects claimed from it. */
const hasThings = computed(() => projectOfChan(props.chan) === 'research')
const doc = computed(() => S.chanDocs[props.chan] ?? { html: '', by: '', at: '' })

// ---- 这个频道的事 ----
const SHOWN_DONE = 3
const allDone = ref(false)
const things = computed(() => S.things.filter((t) => t.chan === props.chan))
const busy = computed(() => things.value.filter((t) => t.status !== 'done'))
const done = computed(() =>
  things.value.filter((t) => t.status === 'done').sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
)
const doneShown = computed(() => (allDone.value ? done.value : done.value.slice(0, SHOWN_DONE)))
const groups = computed(() =>
  [
    { key: 'busy', label: '进行中', rows: busy.value, total: busy.value.length },
    { key: 'done', label: '最近完成', rows: doneShown.value, total: done.value.length },
  ].filter((g) => g.total)
)

// ---- 芝士整理 ----
const ORDER: SummaryKind[] = ['decided', 'found', 'open']
const lines = computed(() => S.chat[props.chan] ?? [])
const summary = computed(() => SUMMARY[props.chan] ?? { upto: '', items: [] })
const upto = computed(() => lines.value.findIndex((l) => l.id === summary.value.upto))
const updated = computed(() => {
  const l = lines.value[upto.value]
  return l ? hhmm(l.at) : ''
})
const summaryGroups = computed(() =>
  ORDER.map((kind) => ({
    kind,
    label: SUMMARY_LABELS[kind],
    items: summary.value.items.filter((i) => i.kind === kind),
  })).filter((g) => g.items.length)
)
const byLine = (id: string) => {
  const l = lines.value.find((x) => x.id === id)
  return l ? `${NAMES[l.who]} ${hhmm(l.at)}` : ''
}
/** `code` spans in a sentence, drawn as code without reaching for v-html. */
const pieces = (text: string) => text.split(/`([^`]+)`/).map((t, i) => ({ t, code: i % 2 === 1 }))
</script>

<template>
  <div class="ot-cdoc" data-zone="doc">
    <div class="ot-cdoc__page">
      <header class="ot-cdoc__head">
        <h1 class="ot-cdoc__title">{{ channel.title }}</h1>
        <span class="ot-cdoc__edited">
          {{ NAMES[doc.by] }} {{ whenText(doc.at) }} 编辑<template v-if="updated">
            · 芝士 {{ updated }} 更新摘要</template
          >
        </span>
      </header>

      <section v-if="hasThings" class="ot-cdoc__block" data-zone="chan-things">
        <div class="ot-cdoc__blockhead">
          <h2 class="ot-cdoc__h">这个频道的事</h2>
          <span class="ot-cdoc__note">实时 · 讨论和任务</span>
        </div>
        <template v-for="g in groups" :key="g.key">
          <h3 class="t-eyebrow-read ot-cdoc__group">{{ g.label }} · {{ g.total }}</h3>
          <button
            v-for="t in g.rows"
            :key="t.id"
            type="button"
            class="ot-cdoc__row"
            :class="{ 'is-on': S.thing === t.id && S.center === 'chan' }"
            :data-thing="t.id"
            @click="openThing(t.id)"
          >
            <TaskStateMark
              :state="stateOf(t)"
              :size="15"
              icon-only
              :class="{ 'ot-wait': t.status === 'wait' }"
              class="ot-cdoc__mark"
            />
            <span class="ot-cdoc__name">{{ t.title }}</span>
            <span class="ot-cdoc__owner"
              ><UserAvatar :name="NAMES[t.owner]" :seed="t.owner" :size="16" />{{ NAMES[t.owner] }}</span
            >
            <span class="ot-cdoc__status" :class="{ 'is-wait': t.status === 'wait' }">{{ statusText(t) }}</span>
            <span class="ot-cdoc__time">{{ relTime(t.at) }}</span>
          </button>
          <button
            v-if="g.key === 'done' && g.total > SHOWN_DONE"
            type="button"
            class="ot-cdoc__more"
            @click="allDone = !allDone"
          >
            {{ allDone ? '收起' : `全部 ${g.total} 件` }}
          </button>
        </template>
        <p v-if="!groups.length" class="ot-cdoc__empty">还没有一件事。在对话里 @芝士 说一句话，就开出一个讨论。</p>
      </section>

      <section v-if="summaryGroups.length" class="ot-cdoc__block ot-cdoc__block--cheese" data-zone="cheese-block">
        <div class="ot-cdoc__blockhead">
          <CheeseAvatar :size="16" name="芝士" handle="cheese" />
          <span class="ot-cdoc__note">芝士整理 · {{ updated }} 更新 · 读了 {{ upto + 1 }} 条消息</span>
        </div>
        <div v-for="g in summaryGroups" :key="g.kind" class="ot-cdoc__sum">
          <h3 class="ot-cdoc__label t-eyebrow-read">{{ g.label }}</h3>
          <ul class="ot-cdoc__list">
            <li v-for="item in g.items" :key="item.line" class="ot-cdoc__item">
              <span
                ><template v-for="(p, i) in pieces(item.text)" :key="i"
                  ><code v-if="p.code">{{ p.t }}</code
                  ><template v-else>{{ p.t }}</template></template
                ></span
              >
              <span class="ot-cdoc__by">（{{ byLine(item.line) }}）</span>
              <button
                type="button"
                class="ot-cdoc__jump"
                title="在对话里看这条消息"
                aria-label="在对话里看这条消息"
                @click="jumpTo(chan, item.line)"
              >
                <v-icon size="14" icon="mdi-arrow-top-right" />
              </button>
              <button
                v-if="item.kind === 'open'"
                type="button"
                class="ot-cdoc__start"
                @click="startFromDoc(chan, item.text)"
              >
                开一件事
              </button>
            </li>
          </ul>
        </div>
      </section>

      <section class="ot-cdoc__people" data-zone="people-doc">
        <p class="ot-cdoc__note ot-cdoc__people-note">大家写的 · 直接改</p>
        <DemoDoc :key="chan" :html="doc.html" @update="editChanDoc(chan, $event)" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.ot-cdoc {
  height: 100%;
  overflow: auto;
}

/* A page to read and write in: one column at reading width. */
.ot-cdoc__page {
  max-width: 720px;
  padding: 20px 32px 48px;
}

.ot-cdoc__head {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 16px;
}

.ot-cdoc__title {
  margin: 0;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  line-height: 1.3;
  color: var(--ink);
}

.ot-cdoc__edited,
.ot-cdoc__note {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-cdoc__block {
  padding: 12px 0;
  border-top: 1px solid var(--line);
}

.ot-cdoc__blockhead {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
}

.ot-cdoc__h {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.ot-cdoc__group {
  margin: 10px 0 2px;
}

.ot-cdoc__row {
  display: flex;
  gap: 10px;
  align-items: center;
  width: 100%;
  height: 32px;
  padding: 0 8px;
  margin: 0 -8px;
  font-size: 14px;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
  box-sizing: content-box;
}

.ot-cdoc__row:hover {
  background: var(--fill);
}

.ot-cdoc__row.is-on {
  background: var(--fill-2);
}

.ot-cdoc__mark {
  flex: none;
}

.ot-cdoc__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-cdoc__owner {
  display: inline-flex;
  flex: none;
  gap: 4px;
  align-items: center;
  font-size: 13px;
  color: var(--text);
}

.ot-cdoc__status,
.ot-cdoc__time {
  flex: none;
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
}

/* 「等你采纳」 is the one state that waits on a person: the only amber. */
.ot-cdoc__status.is-wait {
  color: var(--accent-ink);
}

.ot-cdoc__time {
  width: 72px;
  font-size: 12px;
  text-align: right;
}

.ot-cdoc__more {
  padding: 4px 0;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-cdoc__more:hover {
  color: var(--ink);
}

.ot-cdoc__empty {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--muted);
}

/* 芝士's block reads as 芝士's: a quiet rule down its left edge and its own byline. */
.ot-cdoc__block--cheese {
  padding: 12px 0 12px 16px;
  margin-top: 4px;
  border-top: 0;
  border-left: 2px solid var(--line-2);
}

.ot-cdoc__sum {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  gap: 12px;
  padding: 6px 0;
}

.ot-cdoc__label {
  margin: 0;
  padding-top: 3px;
}

.ot-cdoc__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.ot-cdoc__item {
  font-size: 14px;
  line-height: var(--lh-14-loose);
  color: var(--ink);
}

.ot-cdoc__item code {
  padding: 0 4px;
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-cdoc__by {
  color: var(--muted);
}

.ot-cdoc__jump {
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  margin-left: 2px;
  color: var(--muted);
  vertical-align: -3px;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-cdoc__jump:hover {
  color: var(--ink);
  background: var(--fill);
}

.ot-cdoc__start {
  height: 22px;
  padding: 0 8px;
  margin-left: 8px;
  font: inherit;
  font-size: 12px;
  line-height: 20px;
  color: var(--text);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}

.ot-cdoc__start:hover {
  color: var(--ink);
  background: var(--fill);
}

.ot-cdoc__people {
  padding-top: 12px;
  margin-top: 8px;
  border-top: 1px solid var(--line);
}

.ot-cdoc__people-note {
  margin: 0 0 4px;
}
</style>
