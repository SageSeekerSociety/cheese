<script setup lang="ts">
// The right panel for one 一件事. As a 讨论 it has 对话 / 资料 and a 开工
// button; 开工 turns the same conversation into a 任务 in place: the panel
// widens and gains 现场 / 改动 / 成果, and the stage bar moves along
// 讨论 › 在做 › 等你采纳 › 完成.
import type { PanelTab } from '@/components/panels/PanelTabs.vue'
import type { TodoItem } from '@/cx_types'

import { computed } from 'vue'

import { NAMES, PLAN, RESULTS_MD, TASK_DIFFS, TERM } from './data'
import { block, REFS, stateOf } from './shapes'
import { accept, confirmKick, kickoff, PHASE, PHASES, S, thingById } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import ChangesDiffList from '@/components/panels/ChangesDiffList.vue'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import TodoChecklist from '@/components/panels/TodoChecklist.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'

const thing = computed(() => thingById(S.thing)!)
const talk = computed(() => thing.value.status === 'talk')
const ours = computed(() => thing.value.id === 't4')

const tabs = computed<PanelTab[]>(() => {
  if (talk.value)
    return [
      { key: 'chat', label: '对话', icon: 'mdi-message-outline' },
      { key: 'sources', label: '资料', icon: 'mdi-bookshelf', signal: { kind: 'count', count: 3 } },
    ]
  const running = thing.value.status === 'run'
  return [
    { key: 'chat', label: '对话', icon: 'mdi-message-outline' },
    { key: 'site', label: '现场', icon: 'mdi-monitor-eye', signal: running ? { kind: 'pulse' } : undefined },
    {
      key: 'changes',
      label: '改动',
      icon: 'mdi-file-compare',
      signal: thing.value.status === 'wait' ? { kind: 'count', count: TASK_DIFFS.length, fresh: true } : undefined,
    },
    { key: 'result', label: '成果', icon: 'mdi-table-check', empty: thing.value.status === 'run' },
  ]
})

const tab = computed(() => (tabs.value.some((t) => t.key === S.rtab) ? S.rtab : 'chat'))

interface Said {
  id: string
  who: string
  time: string
  text: string
}

const thread = computed<(Said | { id: string; mark: string; ok?: boolean })[]>(() => {
  const t = thing.value
  if (!ours.value) return [{ id: 'x', who: 'cheese', time: '', text: t.last.replace(/^芝士：/, '') }]
  const out: (Said | { id: string; mark: string; ok?: boolean })[] = [
    {
      id: 'r1',
      who: 'linxiao',
      time: '10:31',
      text: '<@cheese> 把大家的评测脚本统一一下，固定输出 R@1 / R@5 / R@10',
    },
    {
      id: 'r2',
      who: 'cheese',
      time: '10:31',
      text: '我看了项目文件里的 <&eval/retrieval.py> 和王磊的脚本，两边图像预处理不一样。建议统一成一份，Flickr30K 和 COCO 都跑一遍对数。',
    },
  ]
  if (t.replies >= 2)
    out.push({ id: 'r3', who: 'chensiyuan', time: '10:32', text: 'COCO 要用 5K 测试集，口径见《评测口径说明》。' })
  if (t.replies >= 3)
    out.push({
      id: 'r4',
      who: 'cheese',
      time: '10:32',
      text: '好。计划：对齐预处理 → 统一 `eval/retrieval.py` → 跑 Flickr30K 1K 和 COCO 5K → 出结果表。',
    })
  if (PHASE[t.status] >= 1) out.push({ id: 'k', mark: '林晓 10:33 开工 · 交给芝士 · 林晓负责' })
  if (PHASE[t.status] >= 2)
    out.push({
      id: 'r5',
      who: 'cheese',
      time: '10:41',
      text: '做完了：统一后 Flickr30K R@1 = 79.1，和论文的 79.3 基本对上。改动和结果在右边，请你采纳。',
    })
  if (t.status === 'done') out.push({ id: 'a', mark: '林晓 10:44 采纳 · 已合进正式版', ok: true })
  return out
})

const todo = computed<TodoItem[]>(() =>
  PLAN.map((subject, i) => ({
    id: String(i),
    subject,
    status: i < S.live ? 'completed' : i === S.live && thing.value.status === 'run' ? 'in_progress' : 'pending',
  }))
)

const expanded = new Set(['eval/preprocess.py'])

function said(row: unknown): row is Said {
  return !!row && typeof row === 'object' && 'who' in row
}
</script>

<template>
  <aside class="ot-thing" data-zone="right">
    <header class="ot-thing__head" :class="{ 'ot-flash': S.morph }">
      <h2 class="ot-thing__title">{{ thing.title }}</h2>
      <TaskStateMark :state="stateOf(thing)" :size="15" :class="{ 'ot-wait': thing.status === 'wait' }" />
      <span class="ot-head__gap" />
      <BaseButton v-if="talk && ours" id="b-kick" kind="primary" size="sm" @click="kickoff">开工</BaseButton>
      <BaseButton v-else-if="thing.status === 'wait' && ours" id="b-accept" kind="primary" size="sm" @click="accept">
        采纳 · 合进正式版
      </BaseButton>
      <BaseButton kind="ghost" size="sm" icon="mdi-close" aria-label="关闭" title="关闭" @click="S.thing = null" />
    </header>

    <div class="ot-stages">
      <template v-for="(p, i) in PHASES" :key="p">
        <span v-if="i" class="ot-stages__sep">›</span>
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
      <span class="ot-stages__who">{{ talk ? '还没开工' : `${NAMES[thing.owner]} 负责` }}</span>
    </div>

    <Transition name="ot-sheet">
      <div v-if="S.sheet" class="ot-kick">
        <div class="ot-kick__title">开工，交给芝士</div>
        <div class="ot-kick__row">
          <span class="ot-kick__label">负责</span>
          <span class="ot-kick__value"><UserAvatar name="林晓" seed="linxiao" :size="18" />林晓</span>
        </div>
        <div class="ot-kick__row">
          <span class="ot-kick__label">交付</span>
          <span class="ot-kick__value">统一的评测脚本 + 结果表</span>
        </div>
        <p class="ot-kick__note">芝士在自己那份上改，做完你采纳才合进正式版。你也可以随时自己动手改。</p>
        <div class="ot-kick__actions">
          <BaseButton kind="ghost" size="sm" @click="S.sheet = false">取消</BaseButton>
          <BaseButton id="b-confirm" kind="primary" size="sm" @click="confirmKick">确认开工</BaseButton>
        </div>
      </div>
    </Transition>

    <PanelTabs :tabs="tabs" :active="tab" class="ot-thing__tabs" @select="S.rtab = $event" />

    <div class="ot-thing__body">
      <template v-if="tab === 'chat'">
        <template v-for="(row, i) in thread" :key="row.id">
          <RoomMessage
            v-if="said(row)"
            :block="block(row, 'thing')"
            :parent="null"
            :parent-name="null"
            :run-start="i === 0 || !said(thread[i - 1]) || (thread[i - 1] as Said).who !== row.who"
            :mine="row.who === 'linxiao'"
            :topic-id="null"
            :author-name="NAMES[row.who] ?? row.who"
            :avatar="null"
            :is-agent="row.who === 'cheese'"
            :time="row.time"
            :refs="REFS"
            viewer="linxiao"
            :ask-busy="false"
          />
          <div v-else class="ot-mark" :class="{ 'ot-mark--ok': 'ok' in row && row.ok }">
            {{ 'mark' in row ? row.mark : '' }}
          </div>
        </template>
      </template>

      <div v-else-if="tab === 'sources'" class="ot-pad">
        <p class="ot-note">芝士在这段讨论里读过的项目文件，每条写着谁、什么时候放进来的。</p>
        <ul class="ot-list">
          <li>
            <v-icon size="16" icon="mdi-language-python" /><code>eval/retrieval.py</code><span>芝士 10:31 读过</span>
          </li>
          <li>
            <v-icon size="16" icon="mdi-language-python" /><code>eval/preprocess.py</code><span>芝士 10:31 读过</span>
          </li>
          <li><v-icon size="16" icon="mdi-file-document-outline" />评测口径说明<span>陈思远 10:32 引用</span></li>
        </ul>
      </div>

      <div v-else-if="tab === 'site'" class="ot-pad">
        <div class="ot-site__who">
          <CheeseAvatar :size="20" name="芝士" handle="cheese" :state="thing.status === 'run' ? 'work' : 'done'" />
          <span>{{ thing.status === 'run' ? '芝士正在做' : '芝士做完了' }}</span>
        </div>
        <TodoChecklist :items="todo" />
        <div class="ot-note ot-site__term-label">芝士的终端（只看）</div>
        <pre class="ot-term">{{ TERM.slice(0, S.term).join('\n') }}</pre>
      </div>

      <div v-else-if="tab === 'changes'" class="ot-changes">
        <p class="ot-note ot-pad-x">这件事在自己那份上改的东西，采纳后才合进正式版。</p>
        <ChangesDiffList :diffs="TASK_DIFFS" :expanded="expanded" />
      </div>

      <div v-else class="ot-pad">
        <div class="ot-result__path">
          <v-icon size="16" icon="mdi-language-markdown-outline" />results/eval_results.md
        </div>
        <MarkdownView :source="RESULTS_MD" />
        <p class="ot-note">{{ thing.status === 'done' ? '已在项目文件里' : '采纳后进项目文件' }}</p>
      </div>
    </div>

    <div v-if="tab === 'chat'" class="ot-composer">
      <div class="ot-composer__box ot-composer__box--static">
        {{ talk ? '在讨论里说，芝士只出主意、查资料' : '在这件事里继续说' }}
      </div>
    </div>
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
  gap: 8px;
  align-items: center;
  height: 48px;
  padding: 0 8px 0 16px;
  border-bottom: 1px solid var(--line);
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

.ot-stages {
  display: flex;
  gap: 6px;
  align-items: center;
  height: 32px;
  padding: 0 16px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}

.ot-stages__sep {
  color: var(--faint);
}

.ot-stages__step.is-past {
  color: var(--text);
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

.ot-stages__who {
  white-space: nowrap;
}

.ot-kick {
  padding: 12px 16px;
  margin: 12px 16px 0;
  background: var(--accent-wash);
  border: 1px solid var(--accent);
  border-radius: var(--radius-lg);
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
  background: var(--surface);
  border: 1px solid var(--line-2);
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
  padding: 12px 0;
  overflow: auto;
}

.ot-pad {
  padding: 0 16px;
}

.ot-pad-x {
  padding: 0 16px;
}

.ot-note {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.ot-mark {
  padding: 8px 16px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  text-align: center;
}

.ot-mark--ok {
  color: var(--ok-ink);
}

.ot-list {
  padding: 0;
  margin: 0;
  list-style: none;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ot-list li {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--text);
}

.ot-list li + li {
  border-top: 1px solid var(--line);
}

.ot-list li span {
  margin-left: auto;
  font-size: 12px;
  color: var(--muted);
}

.ot-list .v-icon {
  color: var(--faint);
}

.ot-site__who {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.ot-site__term-label {
  margin-top: 16px;
}

.ot-term {
  height: 140px;
  padding: 8px 12px;
  margin: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--code-ink);
  white-space: pre-wrap;
  background: var(--terminal-ground);
  border-radius: var(--radius-md);
}

.ot-changes {
  display: flex;
  flex-direction: column;
  min-height: 100%;
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
