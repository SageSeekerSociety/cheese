<script setup lang="ts">
// 摘要: the channel read for you by 芝士, as a short document — what was decided,
// found, is under way and is still open. Every sentence names who said it and
// when, and ↗ shows that message in 对话, so the summary never has to be trusted
// on its own. An open question becomes a 一件事 the normal way: it is put into
// the channel's composer as an @芝士 message, and sending it opens the 讨论.
import type { SummaryItem, SummaryKind } from './data'
import type { Thing } from './state'

import { computed } from 'vue'

import { NAMES, SUMMARY, SUMMARY_LABELS } from './data'
import { hhmm } from './shapes'
import { jumpTo, openThing, S, sourceOf, thingById } from './state'

const ORDER: SummaryKind[] = ['decided', 'found', 'doing', 'open']

const lines = computed(() => S.chat[S.chan] ?? [])
const summary = computed(() => SUMMARY[S.chan] ?? { upto: '', items: [] })
const upto = computed(() => lines.value.findIndex((l) => l.id === summary.value.upto))
const updated = computed(() => {
  const l = lines.value[upto.value]
  return l ? hhmm(l.at) : ''
})

const STATUS_TEXT = (t: Thing) =>
  t.status === 'wait'
    ? `等${NAMES[t.owner]}采纳`
    : t.status === 'run'
      ? '在跑'
      : t.status === 'talk'
        ? '在讨论'
        : '已完成'

// 在做 also lists a 一件事 opened in this channel since the summary was written:
// its status is live anyway, and leaving it out would make the summary look stale.
const items = computed<SummaryItem[]>(() => {
  const listed = new Set(summary.value.items.map((i) => i.thing).filter(Boolean))
  const fresh = S.things
    .filter((t) => t.chan === S.chan && !listed.has(t.id) && t.status !== 'done')
    .map((t) => ({ kind: 'doing' as const, thing: t.id, line: sourceOf(t.id)?.id ?? '' }))
  return [...summary.value.items, ...fresh]
})
const groups = computed(() =>
  ORDER.map((kind) => ({
    kind,
    label: SUMMARY_LABELS[kind],
    items: items.value.filter((i) => i.kind === kind),
  })).filter((g) => g.items.length)
)

const byLine = (id: string) => {
  const l = lines.value.find((x) => x.id === id)
  return l ? `${NAMES[l.who]} ${hhmm(l.at)}` : ''
}
/** `code` spans in a sentence, drawn as code without reaching for v-html. */
const pieces = (text: string) => text.split(/`([^`]+)`/).map((t, i) => ({ t, code: i % 2 === 1 }))

function startThing(item: SummaryItem) {
  S.chanView[S.chan] = 'talk'
  S.typing = `<@cheese> ${(item.text ?? '').replace(/`/g, '')}`
}
</script>

<template>
  <div class="ot-sum" data-zone="summary">
    <div class="ot-sum__doc">
      <p class="ot-sum__meta">摘要 · 芝士整理 · {{ updated }} 更新 · 读了 {{ upto + 1 }} 条消息</p>
      <section v-for="g in groups" :key="g.kind" class="ot-sum__group" :class="`ot-sum__group--${g.kind}`">
        <h3 class="ot-sum__label t-eyebrow-read">{{ g.label }}</h3>
        <ul class="ot-sum__list">
          <li v-for="item in g.items" :key="item.line + (item.thing ?? '')" class="ot-sum__item">
            <template v-if="item.thing && thingById(item.thing)">
              <button type="button" class="ot-sum__thing" :data-thing="item.thing" @click="openThing(item.thing)">
                <v-icon size="15" icon="mdi-checkbox-marked-outline" class="ot-sum__thing-icon" />{{
                  thingById(item.thing)!.title
                }}</button
              ><span class="ot-sum__status">，{{ STATUS_TEXT(thingById(item.thing)!) }}</span>
            </template>
            <span v-else class="ot-sum__text"
              ><template v-for="(p, i) in pieces(item.text ?? '')" :key="i"
                ><code v-if="p.code">{{ p.t }}</code
                ><template v-else>{{ p.t }}</template></template
              ></span
            >
            <span v-if="item.line" class="ot-sum__by">（{{ byLine(item.line) }}）</span>
            <button
              v-if="item.line"
              type="button"
              class="ot-sum__jump"
              title="在对话里看这条消息"
              aria-label="在对话里看这条消息"
              @click="jumpTo(S.chan, item.line)"
            >
              <v-icon size="14" icon="mdi-arrow-top-right" />
            </button>
            <button v-if="item.kind === 'open'" type="button" class="ot-sum__start" @click="startThing(item)">
              开一件事
            </button>
          </li>
        </ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ot-sum {
  height: 100%;
  overflow: auto;
}

/* A page to read, not a dashboard: one column at reading width, labels in the margin. */
.ot-sum__doc {
  max-width: 680px;
  padding: 24px 32px 40px;
}

.ot-sum__meta {
  margin: 0 0 20px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-sum__group {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}

.ot-sum__label {
  margin: 0;
  padding-top: 2px;
}

.ot-sum__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.ot-sum__item {
  font-size: 14px;
  line-height: var(--lh-14-loose);
  color: var(--ink);
}

.ot-sum__text code {
  padding: 0 4px;
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-sum__by {
  color: var(--muted);
}

.ot-sum__thing {
  display: inline;
  padding: 0;
  font: inherit;
  color: var(--ink);
  text-align: left;
  text-decoration: underline;
  text-decoration-color: var(--line-2);
  text-underline-offset: 3px;
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-sum__thing:hover {
  text-decoration-color: var(--ink);
}

.ot-sum__thing-icon {
  margin-right: 4px;
  color: var(--muted);
  vertical-align: -2px;
}

.ot-sum__status {
  color: var(--text);
}

.ot-sum__jump {
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

.ot-sum__jump:hover {
  color: var(--ink);
  background: var(--fill);
}

.ot-sum__start {
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

.ot-sum__start:hover {
  color: var(--ink);
  background: var(--fill);
}
</style>
