<script setup lang="ts">
// The right panel for one 一件事. As a 讨论 it has 对话 / 资料 and a 开工
// button; 开工 turns the same conversation into a 任务 in place: the panel
// widens and gains 现场 / 改动 / 成果, and the stage bar moves along
// 讨论 › 在做 › 等你采纳 › 完成. The header row, the tabs, the conversation, the
// composer, 现场 and the diff are the product's own components.
import type { PanelTab } from '@/components/panels/PanelTabs.vue'
import type { Block, TodoItem } from '@/cx_types'
import type { MemberActivityLine } from '@/lib/memberActivity'
import type { ChatLine, MarkLine } from './state'

import { computed, ref, watch } from 'vue'

import { CHANNELS, ME, NAMES, PLAN, RESULTS_MD, TASK_DIFFS } from './data'
import DemoComposer from './DemoComposer.vue'
import DemoTimeline from './DemoTimeline.vue'
import { stateOf } from './shapes'
import { siteBlocks, type SiteStep, T4_STEPS } from './site'
import { accept, closeThing, confirmKick, go, kickoff, PHASE, PHASES, S, sayInThing, thingById } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import ChangesDiffList from '@/components/panels/ChangesDiffList.vue'
import PanelSite from '@/components/panels/PanelSite.vue'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import TodoChecklist from '@/components/panels/TodoChecklist.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import { fileIcon } from '@/lib/fileKind'
import { siteBundle } from '@/views/demo/catalogPanelsFixtures'

const thing = computed(() => thingById(S.thing)!)
const talk = computed(() => thing.value.status === 'talk')
const ours = computed(() => thing.value.id === 't4')
const chanTitle = computed(() => CHANNELS.find((c) => c.id === thing.value.chan)?.title ?? '')

const plus = (iso: string, min: number) => new Date(Date.parse(iso) + min * 60_000).toISOString()

/** What was said in the scripted 一件事, up to the step it is at. */
const scripted = computed<(ChatLine | MarkLine)[]>(() => {
  const t = thing.value
  const at = S.askAt || t.at
  const out: (ChatLine | MarkLine)[] = [
    { id: 'r1', who: ME, at, text: '<@cheese> 把大家的评测脚本统一一下，固定输出 R@1 / R@5 / R@10' },
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
      text: '做完了，请你采纳：统一后 **Flickr30K R@1 = 79.1**，和论文的 79.3 基本对上；COCO 5K R@1 = 50.3。改了 2 个文件，结果表在「成果」。',
    })
  if (t.status === 'done') out.push({ id: 'a', mark: `${NAMES[ME]} 采纳 · 已合进正式版`, at: plus(at, 12), ok: true })
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
      { key: 'sources', label: '资料', icon: 'mdi-bookshelf', signal: { kind: 'count', count: SOURCES.length } },
    ]
  const diffs = ours.value ? TASK_DIFFS : []
  return [
    { key: 'chat', label: '对话', icon: 'mdi-message-outline' },
    { key: 'site', label: '现场', icon: 'mdi-monitor-eye', signal: t.status === 'run' ? { kind: 'pulse' } : undefined },
    {
      key: 'changes',
      label: '改动',
      icon: 'mdi-file-compare',
      empty: !diffs.length,
      signal: diffs.length && t.status === 'wait' ? { kind: 'count', count: diffs.length, fresh: true } : undefined,
    },
    { key: 'result', label: '成果', icon: 'mdi-table-check', empty: !result.value },
  ]
})
const tab = computed(() => (tabs.value.some((t) => t.key === S.rtab) ? S.rtab : 'chat'))

// ---- 资料 ----
const SOURCES = [
  { path: 'eval/retrieval.py', who: 'cheese', what: '读过' },
  { path: 'eval/preprocess.py', who: 'cheese', what: '读过' },
  { path: 'docs/评测口径说明.md', who: 'chensiyuan', what: '引用' },
]

// ---- 现场 ----
const OTHER_SITE: Record<string, SiteStep[]> = {
  t1: [
    { kind: 'thinking', text: '用 CLIP 的 tokenizer 数 token，训练集和 5K 测试集分开。' },
    {
      kind: 'bash',
      command: 'python scripts/caption_lengths.py --split train2017 --split 5k',
      description: '统计描述长度',
      output: 'train2017: 591753 captions  >77: 37281 (6.3%)\n5k:        25010 captions  >77:  1526 (6.1%)',
    },
    { kind: 'text', text: '超过 77 个 token 的占 6.3%，表放进成果。' },
  ],
  t2: [
    {
      kind: 'bash',
      command: 'python -m eval.retrieval --model blip_base_14M --data flickr30k --split 1k',
      description: '跑第 3 个 checkpoint',
      output: 'encoding 1000 images, 5000 captions … done (41s)\nR@1 81.9  R@5 95.4  R@10 97.8',
    },
    {
      kind: 'bash',
      command: 'python -m eval.retrieval --model blip_base_129M --data flickr30k --split 1k',
      description: '跑第 4 个 checkpoint',
      output: 'loading checkpoint blip_base_129M … 38%',
    },
  ],
  t3: [
    { kind: 'read', path: 'docs/组会纪要-11-07.md', output: '组会纪要 11-07\n1. 评测口径：统一用 eval/retrieval.py …' },
    {
      kind: 'bash',
      command: 'marp slides.md -o report/组会-11-07.pdf',
      description: '导出幻灯片',
      output: '[  INFO ] slides.md => report/组会-11-07.pdf (7 pages)',
    },
  ],
}
const siteRecords = computed<Block[]>(() => {
  const t = thing.value
  if (ours.value)
    return siteBlocks(
      't4',
      T4_STEPS.slice(0, Math.max(S.live, 0) + (t.status === 'run' ? 1 : 0)).flat(),
      plus(S.askAt || t.at, 2)
    )
  return siteBlocks(t.id, OTHER_SITE[t.id] ?? [], plus(t.at, -20))
})
const site = siteBundle({ transcript: siteRecords })
// The session strip at the top of 现场 says who is connected: 芝士 while the task runs, nobody after.
watch(
  () => thing.value.status === 'run',
  (on) => {
    site.inspector.state.value = on
      ? ({ id: `${thing.value.id}-session`, connected: true, tasks: {} } as typeof site.inspector.state.value)
      : null
  },
  { immediate: true }
)
const working = computed(() => thing.value.status === 'run')
const running = computed(() => (working.value ? { [`${thing.value.id}-turn`]: Date.now() - 60_000 } : {}))
const memberNames = NAMES
const STEP_NOW = [
  '正在读文件',
  '执行命令 pytest',
  '执行命令 eval.retrieval flickr30k',
  '执行命令 eval.retrieval coco',
  '正在写结果表',
]
const activity = computed<MemberActivityLine[]>(() =>
  working.value
    ? [
        {
          handle: 'cheese',
          name: '芝士',
          kind: 'working',
          since: Math.floor(Date.parse(thing.value.at) / 1000),
          step: ours.value ? STEP_NOW[Math.min(S.live, 4)] ?? null : '执行命令 eval.retrieval',
        },
      ]
    : []
)

const todo = computed<TodoItem[]>(() =>
  PLAN.map((subject, i) => ({
    id: String(i),
    subject,
    status: thing.value.status !== 'run' || i < S.live ? 'completed' : i === S.live ? 'in_progress' : 'pending',
  }))
)

// ---- 改动 / 成果 ----
const expanded = ref(new Set(['eval/preprocess.py']))
function toggleSection(path: string) {
  const next = new Set(expanded.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expanded.value = next
}
const result = computed(() => {
  const t = thing.value
  if (ours.value) return t.status === 'wait' || t.status === 'done' ? RESULTS_MD : ''
  return t.result ?? ''
})

// ---- conversation ----
const replyLine = computed(() => lines.value.find((l) => l.id === S.thingReplyTo) as ChatLine | undefined)
const replyLabel = computed(() =>
  replyLine.value ? `回复 ${NAMES[replyLine.value.who]}：${replyLine.value.text.slice(0, 40)}` : null
)

const ownerLabel = computed(() => (talk.value ? null : NAMES[thing.value.owner]))
</script>

<template>
  <aside class="ot-thing" data-zone="right">
    <header class="ot-thing__head" :class="{ 'ot-flash': S.morph }">
      <v-icon size="18" class="ot-thing__icon" :icon="talk ? 'mdi-forum-outline' : 'mdi-checkbox-marked-outline'" />
      <span v-if="S.focus" class="ot-thing__chan"># {{ chanTitle }} /</span>
      <h2 class="ot-thing__title" :title="thing.title">{{ thing.title }}</h2>
      <TaskStateMark :state="stateOf(thing)" :size="15" :class="{ 'ot-wait': thing.status === 'wait' }" />
      <span class="ot-head__gap" />
      <span v-if="ownerLabel" class="ot-thing__owner" :title="`${ownerLabel} 负责`">
        <UserAvatar :name="ownerLabel" :seed="thing.owner" :size="20" />{{ ownerLabel }} 负责
      </span>
      <BaseButton v-if="talk && ours" id="b-kick" kind="primary" size="sm" @click="kickoff">开工</BaseButton>
      <BaseButton v-else-if="thing.status === 'wait' && ours" id="b-accept" kind="primary" size="sm" @click="accept">
        采纳 · 合进正式版
      </BaseButton>
      <BaseButton
        kind="ghost"
        size="sm"
        :icon="S.focus ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'"
        :title="S.focus ? '退出专注' : '专注模式'"
        :aria-label="S.focus ? '退出专注' : '专注模式'"
        @click="S.focus = !S.focus"
      />
      <BaseButton kind="ghost" size="sm" icon="mdi-close" title="关闭" aria-label="关闭" @click="closeThing" />
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
          <span class="ot-kick__value">统一的评测脚本 + 结果表</span>
        </div>
        <p class="ot-kick__note">芝士在这件事自己的那份上改，你采纳后才合进正式版。</p>
        <div class="ot-kick__actions">
          <BaseButton kind="ghost" size="sm" @click="S.sheet = false">取消</BaseButton>
          <BaseButton id="b-confirm" kind="primary" size="sm" @click="confirmKick">确认开工</BaseButton>
        </div>
      </div>
    </Transition>

    <PanelTabs :tabs="tabs" :active="tab" class="ot-thing__tabs" @select="S.rtab = $event" />

    <div class="ot-thing__body" :class="{ 'ot-thing__body--flush': tab === 'site' || tab === 'changes' }">
      <DemoTimeline
        v-if="tab === 'chat'"
        :lines="lines"
        :conversation="thing.id"
        @reply="(l) => (S.thingReplyTo = l.id)"
      />

      <div v-else-if="tab === 'sources'" class="ot-pad">
        <button
          v-for="s in SOURCES"
          :key="s.path"
          type="button"
          class="ot-src"
          @click="go('files', { file: s.path, ftab: 'history' })"
        >
          <v-icon size="18" :icon="fileIcon(s.path)" />
          <span class="ot-src__name">{{ s.path }}</span>
          <span class="ot-src__who">{{ NAMES[s.who] }} {{ s.what }}</span>
        </button>
      </div>

      <div v-else-if="tab === 'site'" class="ot-site">
        <div v-if="ours" class="ot-site__plan">
          <TodoChecklist :items="todo" />
        </div>
        <PanelSite
          class="ot-site__panel"
          :site="site"
          :topic-id="thing.id"
          :active="true"
          :member-names="memberNames"
          :working="working"
          :running-turns="running"
          :activity="activity"
          agent-name="芝士"
        />
      </div>

      <template v-else-if="tab === 'changes'">
        <ChangesDiffList
          v-if="ours"
          :diffs="TASK_DIFFS"
          :expanded="expanded"
          @toggle-section="toggleSection"
          @open="(p: string) => go('files', { file: p, ftab: 'history' })"
        />
        <BaseEmptyState v-else size="compact" icon="mdi-file-compare" title="这件事没有改动文件" />
      </template>

      <div v-else class="ot-pad">
        <template v-if="result">
          <div v-if="ours" class="ot-result__path">
            <v-icon size="16" icon="mdi-language-markdown-outline" />results/eval_results.md
            <span class="ot-head__gap" />
            <button
              v-if="thing.status === 'done'"
              type="button"
              class="ot-result__open"
              @click="go('files', { file: 'results/eval_results.md', ftab: 'history' })"
            >
              在项目文件里打开
            </button>
          </div>
          <MarkdownView :source="result" />
        </template>
        <BaseEmptyState
          v-else
          size="compact"
          icon="mdi-table-check"
          title="还没有成果"
          description="做完之后，交付的东西会出现在这里"
        />
      </div>
    </div>

    <DemoComposer
      v-if="tab === 'chat'"
      v-model="S.thingTyping"
      :placeholder="talk ? '回复讨论，@芝士 问它' : '在任务中回复'"
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

.ot-thing__owner {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  margin-right: 4px;
  font-size: 13px;
  color: var(--text);
  white-space: nowrap;
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

.ot-site {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.ot-site__plan {
  padding: 12px 16px 8px;
  border-bottom: 1px solid var(--line);
}

.ot-site__panel {
  flex: 1;
  min-height: 0;
}

.ot-result__path {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 8px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
}

.ot-result__open {
  padding: 0;
  font-family: var(--font-sans);
  font-size: 13px;
  color: var(--accent-ink);
  cursor: pointer;
  background: none;
  border: 0;
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
