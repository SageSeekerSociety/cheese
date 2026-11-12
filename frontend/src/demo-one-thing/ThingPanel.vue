<script setup lang="ts">
// The panel for one 一件事. As a 讨论 it has 对话 / 资料 and a 开工 button;
// 开工 turns the same conversation into a 任务 in place: the panel widens and
// gains 工作台 and 改动, and the stage bar moves along 讨论 › 在做 › 等你采纳
// › 完成. The header row, the tabs, the conversation, the composer and the
// diff are the product's own components.
// 工作台 (Workbench.vue) is where the 一件事 is done: its copy of the files,
// and every 运行 with what it printed. Where it opens is 方案「工作台放哪」: W1
// in the center column (this panel keeps 对话 / 改动 and a 「工作台」 button
// in the header), W2 as a tab here, the panel widening while a person is
// doing it, W3 with this panel as the 一件事's own full view (tabs 工作台 ·
// 对话 · 改动, a back arrow to the channel), which a started 一件事 always
// opens as.
// The header holds who is doing it and what moves the doing along, the same
// for every 一件事 (ThingActions: 开工, 接手, 交给芝士, 改好了, 采纳); under W1,
// while the 工作台 is open in the center, that row moves to the 工作台's own
// header and this one keeps the title. The 交给芝士 sheet opens here.
// 改动 is the copy against 正式版, and the one place a copy is 采纳ed
// (acceptCopy). When 正式版 moved since the copy was taken, 改动 says
// 「正式版在这期间改过」: changes to different lines are kept on both sides
// (the copy catches up first, a line in 对话), and where both changed the
// same lines it shows both and the adopter picks a side per block before 采纳.
import type { PanelTab } from '@/components/panels/PanelTabs.vue'
import type { ChatLine, MarkLine } from './state'

import { computed, ref, watch } from 'vue'

import { channelOf, diffText, ME, NAMES, TASK_DIFFS } from './data'
import DemoComposer from './DemoComposer.vue'
import DemoTimeline from './DemoTimeline.vue'
import { hhmm, stateOf } from './shapes'
import {
  acceptCopy,
  canTake,
  canWork,
  closeThing,
  confirmKick,
  copyState,
  doingNow,
  handToCheese,
  hasBench,
  jumpTo,
  openBench,
  openFile,
  PHASE,
  PHASES,
  pickSide,
  S,
  sayInThing,
  sourceOf,
  tellCheese,
  thingById,
} from './state'
import ThingActions from './ThingActions.vue'
import { V } from './variants'
import Workbench from './Workbench.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import ChangesDiffList from '@/components/panels/ChangesDiffList.vue'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import { fileIcon } from '@/lib/fileKind'

const thing = computed(() => thingById(S.thing)!)
const talk = computed(() => thing.value.status === 'talk')
const ours = computed(() => thing.value.id === 't4')
const bench = computed(() => hasBench(thing.value))
/** 方案 W3: a started 一件事 is its own full view. */
const full = computed(() => V.bench === 'W3' && !talk.value)
/** 方案 W1: the 工作台 opens in the center column, beside this panel. */
const benchInCenter = computed(() => V.bench === 'W1')
/** I can work in it now (I am doing it, or took it over, or my team is doing it). */
const working = computed(() => canWork(thing.value))
/** Who is doing it now, once started. */
const who = computed(() => doingNow(thing.value))
const chanTitle = computed(() => channelOf(thing.value.chan)?.title ?? '')
/** The channel message this 一件事 was opened under: 来自 points back at it. */
const source = computed(() => sourceOf(thing.value.id))

const plus = (iso: string, min: number) => new Date(Date.parse(iso) + min * 60_000).toISOString()

/** What was said in the scripted 一件事, up to the step it is at. */
const scripted = computed<(ChatLine | MarkLine)[]>(() => {
  const t = thing.value
  const at = S.askAt || t.at
  const out: (ChatLine | MarkLine)[] = [
    {
      id: 'r1',
      who: ME,
      at,
      text: source.value?.text ?? '<@cheese> 把大家的评测脚本统一一下，固定输出 R@1 / R@5 / R@10',
    },
    {
      id: 'r2',
      who: 'cheese',
      at: plus(at, 0.4),
      text: '我看了项目文件里的 `eval/retrieval.py`、`eval/preprocess.py` 和王磊 notebook 里那份，两边图像预处理不一样：\n\n- 项目里：`Resize(256)`，没有 center crop\n- 王磊的：`Resize(224, BICUBIC)` + `CenterCrop(224)`，和论文一致\n\n建议统一成论文那种，Flickr30K 和 COCO 都重跑一遍对数。',
    },
  ]
  if (t.replies >= 2)
    out.push({ id: 'r3', who: 'chensiyuan', at: plus(at, 1), text: 'COCO 要用 5K 测试集，口径见《评测口径说明》' })
  if (t.replies >= 3)
    out.push({
      id: 'r4',
      who: 'cheese',
      at: plus(at, 1.3),
      text: '好。计划：\n1. 对齐预处理（224，BICUBIC，center crop）\n2. 统一 `eval/retrieval.py`，输出 R@1/5/10\n3. 跑 Flickr30K 1K 和 COCO 5K\n4. 结果表写进 `results/eval_results.md`',
    })
  if (PHASE[t.status] >= 1) out.push({ id: 'k', mark: `${NAMES[ME]} 开工 · 交给芝士`, at: plus(at, 2) })
  if (PHASE[t.status] >= 2)
    out.push({
      id: 'r5',
      who: 'cheese',
      at: plus(at, 9),
      text: '做完了，请你采纳：统一后 **Flickr30K R@1 = 79.1**，和论文的 79.3 基本对上；COCO 5K R@1 = 50.3。改了 2 个文件，结果表 `results/eval_results.md` 在工作台里。',
    })
  return out
})
const lines = computed(() => [
  ...(ours.value ? scripted.value : thing.value.talk ?? []),
  ...(S.extra[thing.value.id] ?? []),
])

const tabs = computed<PanelTab[]>(() => {
  const t = thing.value
  if (talk.value)
    return [
      { key: 'chat', label: '对话', icon: 'mdi-message-outline' },
      { key: 'sources', label: '资料', icon: 'mdi-bookshelf', signal: { kind: 'count', count: SOURCES.value.length } },
    ]
  const n = diffs.value.length
  const benchTab: PanelTab = {
    key: 'bench',
    label: '工作台',
    icon: 'mdi-tools',
    signal: who.value === 'cheese' ? ({ kind: 'pulse' } as const) : undefined,
  }
  const chat: PanelTab = { key: 'chat', label: '对话', icon: 'mdi-message-outline' }
  return [
    // The full view leads with the 工作台, as a document leads with its page.
    ...(full.value && bench.value ? [benchTab, chat] : [chat]),
    ...(!full.value && !benchInCenter.value && bench.value ? [benchTab] : []),
    {
      key: 'changes',
      label: '改动',
      icon: 'mdi-file-compare',
      empty: !n,
      signal: n && t.status === 'wait' ? { kind: 'count', count: n, fresh: true } : undefined,
    },
  ]
})
/** The tab on show: the 工作台 while it is open here (W2, W3), else the one picked, else 对话. */
const tab = computed(() => {
  if (S.bench && bench.value && !benchInCenter.value && !talk.value) return 'bench'
  const k = S.rtab === 'bench' ? 'chat' : S.rtab
  return tabs.value.some((t) => t.key === k) ? k : 'chat'
})
function selectTab(key: string) {
  if (key === 'bench') {
    S.bench = true
    return
  }
  S.rtab = key
  // In W1 the 工作台 stays in the center column whatever this panel shows.
  if (!benchInCenter.value) S.bench = false
}

/** What the 一件事 changed: the scripted task's files, or each file of its copy against 正式版 as it reads now
 *  (what 采纳 would do), and once 采纳ed against what 正式版 read then. Files it has not changed are left out. */
const states = computed(() => (thing.value.copy ? copyState(thing.value) : []))
/** 正式版 moved since the copy was taken. */
const stale = computed(() => states.value.some((x) => x.stale))
/** Blocks both changed and nobody has picked a side for yet: 采纳 waits on them. */
const open = computed(() => states.value.reduce((n, x) => n + x.open, 0))
const blocks = computed(() => states.value.reduce((n, x) => n + x.blocks.length, 0))
const staleBy = computed(() => [...new Set(states.value.flatMap((x) => x.by))].map((h) => NAMES[h]).join('、'))
const doerName = computed(() => NAMES[thing.value.doer ?? 'cheese'])
const diffs = computed(() => {
  const t = thing.value
  if (ours.value && t.status !== 'run' && !stale.value) return TASK_DIFFS
  return [
    ...states.value
      .map((x) => ({ path: x.path, before: t.status === 'done' ? t.copy![x.path]!.base : x.now, after: x.text }))
      .filter((x) => x.before !== x.after)
      .map((x) => diffText(x.path, x.before, x.after)),
    ...Object.entries(ours.value ? {} : t.adds ?? {}).map(([path, md]) => diffText(path, '', md)),
  ]
})
const acceptThis = () => acceptCopy(thing.value.id)

// ---- 资料 ----
const SOURCES = computed(() =>
  ours.value
    ? [
        { path: 'eval/retrieval.py', who: 'cheese', what: '读过' },
        { path: 'eval/preprocess.py', who: 'cheese', what: '读过' },
        { path: 'docs/评测口径说明.md', who: 'chensiyuan', what: '引用' },
      ]
    : Object.keys(thing.value.copy ?? {}).map((path) => ({ path, who: thing.value.owner, what: '引用' }))
)

// ---- 改动 ----
const expanded = ref(new Set<string>())
// Each 一件事 opens on its first changed file.
watch(
  () => thing.value.id,
  () => (expanded.value = new Set([Object.keys(thing.value.copy ?? {})[0] ?? 'eval/preprocess.py'])),
  { immediate: true }
)
function toggleSection(path: string) {
  const next = new Set(expanded.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expanded.value = next
}

// ---- conversation ----
const replyLine = computed(() => lines.value.find((l) => l.id === S.thingReplyTo) as ChatLine | undefined)
const replyLabel = computed(() =>
  replyLine.value ? `回复 ${NAMES[replyLine.value.who]}：${replyLine.value.text.slice(0, 40)}` : null
)
</script>

<template>
  <aside class="ot-thing" :class="{ 'ot-thing--full': full }" data-zone="right">
    <header class="ot-thing__head" :class="{ 'ot-flash': S.morph }">
      <BaseButton
        v-if="full"
        id="b-thing-back"
        kind="ghost"
        size="sm"
        prepend-icon="mdi-arrow-left"
        :title="`回到 # ${chanTitle}`"
        @click="closeThing"
        ># {{ chanTitle }}</BaseButton
      >
      <v-icon
        v-else
        size="18"
        class="ot-thing__icon"
        :icon="talk ? 'mdi-forum-outline' : 'mdi-checkbox-marked-outline'"
      />
      <span v-if="S.focus && !full" class="ot-thing__chan"># {{ chanTitle }} /</span>
      <h2 class="ot-thing__title" :title="thing.title">{{ thing.title }}</h2>
      <TaskStateMark :state="stateOf(thing)" :size="15" :class="{ 'ot-wait': thing.status === 'wait' }" />
      <span class="ot-head__gap" />
      <ThingActions v-if="!(benchInCenter && S.bench && bench)" :faces="!full" />
      <BaseButton
        v-if="benchInCenter && bench && !full"
        id="b-bench"
        kind="ghost"
        size="sm"
        :prepend-icon="S.bench ? undefined : 'mdi-tools'"
        :icon="S.bench ? 'mdi-tools' : undefined"
        :class="{ 'ot-thing__on': S.bench }"
        :aria-pressed="S.bench"
        :aria-label="S.bench ? '工作台开在中间，点一下收起' : undefined"
        :title="S.bench ? '工作台开在中间，点一下收起' : '在中间打开这件事的工作台'"
        @click="S.bench ? (S.bench = false) : openBench(thing.id)"
        >{{ S.bench ? '' : '工作台' }}</BaseButton
      >
      <BaseButton
        v-if="!full && !(benchInCenter && S.bench)"
        kind="ghost"
        size="sm"
        :icon="S.focus ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'"
        :title="S.focus ? '退出专注' : '专注模式'"
        :aria-label="S.focus ? '退出专注' : '专注模式'"
        @click="S.focus = !S.focus"
      />
      <BaseButton
        v-if="!full"
        kind="ghost"
        size="sm"
        icon="mdi-close"
        title="关闭"
        aria-label="关闭"
        @click="closeThing"
      />
    </header>

    <div class="ot-stages">
      <template v-for="(p, i) in PHASES" :key="p">
        <v-icon v-if="i" size="14" class="ot-stages__sep" icon="mdi-chevron-right" />
        <span
          class="ot-stages__step"
          :class="{
            'is-on': i === PHASE[thing.status],
            'is-past': i < PHASE[thing.status],
            'is-wait': i === 2 && thing.status === 'wait',
          }"
          >{{ p }}</span
        >
      </template>
      <span class="ot-head__gap" />
      <button
        v-if="source"
        type="button"
        class="ot-from"
        title="在频道里看这条消息"
        @click="jumpTo(thing.chan, source.id)"
      >
        来自 # {{ chanTitle }} · {{ NAMES[source.who] }} {{ hhmm(source.at) }}
        <v-icon size="13" icon="mdi-arrow-top-right" />
      </button>
    </div>

    <Transition name="ot-sheet">
      <div v-if="S.sheet" class="ot-kick">
        <div class="ot-kick__title">开工，交给芝士</div>
        <div class="ot-kick__row">
          <span class="ot-kick__label">负责</span>
          <span class="ot-kick__value"><UserAvatar :name="NAMES[ME]" :seed="ME" :size="18" />{{ NAMES[ME] }}</span>
        </div>
        <div class="ot-kick__row">
          <span class="ot-kick__label">交付</span>
          <span class="ot-kick__value">{{ thing.deliver ?? '统一的评测脚本 + 结果表' }}</span>
        </div>
        <p class="ot-kick__note">芝士在这件事的工作台里做，你随时能打开看、接手；你采纳后才合进正式版。</p>
        <div class="ot-kick__actions">
          <BaseButton kind="ghost" size="sm" @click="S.sheet = false">取消</BaseButton>
          <BaseButton id="b-confirm" kind="primary" size="sm" @click="confirmKick">确认开工</BaseButton>
        </div>
      </div>
    </Transition>
    <Transition name="ot-sheet">
      <div v-if="S.hand" class="ot-kick" data-zone="hand">
        <div class="ot-kick__title">交给芝士</div>
        <textarea
          v-model="S.handNote"
          class="ot-kick__note-in"
          rows="2"
          autocomplete="off"
          placeholder="跟芝士说一句：改了什么、接下来做什么"
          aria-label="交给芝士时说的话"
          data-hand-note
        />
        <p class="ot-kick__note">芝士在这个工作台里接着做，你改的都留着。</p>
        <div class="ot-kick__actions">
          <BaseButton kind="ghost" size="sm" @click="S.hand = false">取消</BaseButton>
          <BaseButton id="b-hand-confirm" kind="primary" size="sm" @click="handToCheese(thing.id, S.handNote)"
            >交给芝士</BaseButton
          >
        </div>
      </div>
    </Transition>

    <PanelTabs :tabs="tabs" :active="tab" class="ot-thing__tabs" @select="selectTab" />

    <div class="ot-thing__body" :class="{ 'ot-thing__body--flush': tab === 'bench' || tab === 'changes' }">
      <Workbench v-if="tab === 'bench'" :mode="full ? 'full' : 'panel'" />

      <DemoTimeline
        v-else-if="tab === 'chat'"
        :lines="lines"
        :conversation="thing.id"
        @reply="(l) => (S.thingReplyTo = l.id)"
      />

      <div v-else-if="tab === 'sources'" class="ot-pad">
        <button v-for="s in SOURCES" :key="s.path" type="button" class="ot-src" @click="openFile(s.path)">
          <v-icon size="18" :icon="fileIcon(s.path)" />
          <span class="ot-src__name">{{ s.path }}</span>
          <span class="ot-src__who">{{ NAMES[s.who] }} {{ s.what }}</span>
        </button>
      </div>

      <template v-else>
        <div v-if="thing.copy && thing.status !== 'talk'" class="ot-chg" data-zone="task-changes">
          <div class="ot-chg__bar">
            <v-icon size="15" :icon="working ? 'mdi-pencil-outline' : 'mdi-lock-outline'" />
            <span class="ot-chg__what">{{
              thing.status === 'done'
                ? '已采纳，进了正式版'
                : working
                  ? '你在工作台里改的 · 和正式版的差别'
                  : `${who === 'cheese' || !who ? doerName : NAMES[who]}在工作台里改的 · 和正式版的差别`
            }}</span>
            <span class="ot-head__gap" />
            <BaseButton
              v-if="bench && thing.status !== 'done'"
              kind="secondary"
              size="sm"
              @click="openBench(thing.id)"
              >{{ working ? '去工作台里改' : '看工作台' }}</BaseButton
            >
            <BaseButton
              v-if="thing.status === 'wait'"
              id="b-accept-tab"
              kind="primary"
              size="sm"
              :disabled="open > 0"
              @click="acceptThis"
              >采纳</BaseButton
            >
          </div>
          <p v-if="!working && thing.status !== 'done' && !thing.course" class="ot-chg__hint">
            要改哪里，<button type="button" class="ot-chg__tell" @click="tellCheese">在对话里告诉芝士</button
            >，它在工作台里接着改{{ thing.status === 'run' && canTake(thing) ? '；或者「接手」自己改' : '' }}。
          </p>
        </div>
        <div v-if="stale" class="ot-stale" data-zone="stale">
          <div class="ot-stale__head">
            <v-icon size="15" icon="mdi-source-merge" />正式版在这期间改过{{ staleBy ? `（${staleBy}采纳的）` : '' }}
          </div>
          <p class="ot-stale__note">
            {{
              blocks
                ? '两边改了不同地方的，采纳时都留着；下面这几行两边都改了，挑一边再采纳。'
                : `两边改的不是同一处：采纳时先跟上最新的正式版，这期间的改动都留着。`
            }}
          </p>
          <template v-for="x in states" :key="x.path">
            <div v-for="(b, k) in x.blocks" :key="k" class="ot-conf" :data-conflict="`${x.path}:${k}`">
              <div class="ot-conf__title">
                <code>{{ x.path }}</code> · 两边都改了这几行{{ x.picks[k] ? '' : '，挑一边' }}
              </div>
              <div class="ot-conf__sides">
                <button
                  v-for="side in ['ours', 'theirs'] as const"
                  :key="side"
                  type="button"
                  class="ot-conf__side"
                  :class="{ 'is-on': x.picks[k] === side, 'is-off': x.picks[k] && x.picks[k] !== side }"
                  :data-pick="side"
                  :aria-pressed="x.picks[k] === side"
                  @click="pickSide(thing.id, x.path, k, side)"
                >
                  <span class="ot-conf__label">
                    <v-icon size="14" :icon="x.picks[k] === side ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank'" />{{
                      side === 'ours'
                        ? `正式版现在的${x.by.length ? `（${x.by.map((h) => NAMES[h]).join('、')}）` : ''}`
                        : '这件事的'
                    }}
                  </span>
                  <pre class="ot-conf__code">{{
                    (side === 'ours' ? b.ours : b.theirs).join('\n') || '（删掉了这几行）'
                  }}</pre>
                </button>
              </div>
            </div>
          </template>
        </div>
        <ChangesDiffList
          v-if="diffs.length"
          :diffs="diffs"
          :expanded="expanded"
          @toggle-section="toggleSection"
          @open="(p: string) => openFile(p)"
        />
        <BaseEmptyState v-else size="compact" icon="mdi-file-compare" title="这件事还没有改动文件" />
      </template>
    </div>

    <DemoComposer
      v-if="tab === 'chat'"
      v-model="S.thingTyping"
      :placeholder="
        talk ? '回复讨论，@芝士 问它' : thing.course ? '在这件事里说，@芝士 问思路（它只给提示）' : '在任务中回复'
      "
      :reply-label="replyLabel"
      @clear-reply="S.thingReplyTo = null"
      @send="sayInThing"
    />
  </aside>
</template>

<style scoped>
.ot-thing {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-thing__head {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  height: 48px;
  padding: 0 8px 0 16px;
  border-bottom: 1px solid var(--line);
}

.ot-thing__icon {
  flex: none;
  color: var(--muted);
}

.ot-thing__chan {
  flex: none;
  font-size: 15px;
  color: var(--muted);
}

.ot-thing__title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ot-thing__head > :not(.ot-thing__title, .ot-head__gap) {
  flex: none;
}

.ot-thing__on {
  background: var(--fill-2);
}

.ot-stages {
  display: flex;
  flex: none;
  gap: 4px;
  align-items: center;
  height: 32px;
  padding: 0 16px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
  border-bottom: 1px solid var(--line);
}

.ot-stages > :not(.ot-from, .ot-head__gap) {
  flex: none;
}

/* Where this came from: quiet, the same grey as the steps behind it, darker on hover. */
.ot-from {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  min-width: 0;
  padding: 0 4px;
  overflow: hidden;
  font: inherit;
  color: var(--muted);
  white-space: nowrap;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.ot-from:hover {
  color: var(--ink);
  background: var(--fill);
}

.ot-stages__sep {
  color: var(--faint);
}

.ot-stages__step.is-past {
  color: var(--muted);
}

.ot-stages__step.is-on {
  font-weight: 600;
  color: var(--ink);
}

/* The one state that waits on a person: the only amber in the bar. */
.ot-stages__step.is-wait {
  padding: 0 6px;
  color: var(--accent-ink);
  background: var(--accent-wash);
  border-radius: var(--radius-sm);
}

.ot-kick {
  padding: 12px 16px;
  margin: 12px 16px 0;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}

.ot-kick__title {
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.ot-kick__row {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 4px 0;
  font-size: 13px;
}

.ot-kick__label {
  width: 32px;
  color: var(--muted);
}

.ot-kick__value {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 2px 8px;
  color: var(--ink);
  background: var(--fill);
  border-radius: var(--radius-md);
}

.ot-kick__note {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-kick__note-in {
  display: block;
  width: 100%;
  padding: 6px 8px;
  font: inherit;
  font-size: 13px;
  color: var(--ink);
  resize: none;
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  outline: none;
}

.ot-kick__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 8px;
}

.ot-thing__tabs {
  flex: none;
}

.ot-thing__body {
  flex: 1;
  min-height: 0;
  padding: 8px 0;
  overflow: auto;
}

.ot-thing__body--flush {
  padding: 0;
}

.ot-pad {
  padding: 8px 16px;
}

.ot-chg {
  border-bottom: 1px solid var(--line);
}

.ot-chg__bar {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 40px;
  padding: 4px 8px 4px 16px;
  font-size: 13px;
  color: var(--muted);
  background: var(--fill);
}

.ot-chg__bar > :not(.ot-chg__what, .ot-head__gap) {
  flex: none;
}

.ot-chg__what {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-chg__hint {
  margin: 0;
  padding: 6px 16px 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill);
}

.ot-chg__tell {
  padding: 0;
  font: inherit;
  color: var(--accent-ink);
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-stale {
  padding: 8px 12px 12px;
  border-bottom: 1px solid var(--line);
}

.ot-stale__head {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--accent-ink);
}

.ot-stale__note {
  margin: 4px 0 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-conf + .ot-conf {
  margin-top: 8px;
}

.ot-conf__title {
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--text);
}

.ot-conf__title code {
  font-family: var(--font-mono);
}

/* The two sides of one block, side by side: the picked one carries the ink, the other steps back. */
.ot-conf__sides {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.ot-conf__side {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 6px 8px;
  font: inherit;
  text-align: left;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.ot-conf__side:hover {
  border-color: var(--faint);
}

.ot-conf__side.is-on {
  border-color: var(--ink);
}

.ot-conf__side.is-off {
  opacity: 0.55;
}

.ot-conf__label {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 12px;
  color: var(--ink);
}

.ot-conf__code {
  margin: 0;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  color: var(--text);
  white-space: pre;
}

.ot-src {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  padding: 8px;
  font-size: 13px;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
}

.ot-src:hover {
  background: var(--fill);
}

.ot-src .v-icon {
  color: var(--muted);
}

.ot-src__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ot-src__who {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}

.ot-sheet-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.ot-sheet-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.ot-sheet-enter-from {
  opacity: 0;
  transform: translateY(-6px);
}

.ot-sheet-leave-to {
  opacity: 0;
}
</style>
