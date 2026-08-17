<script setup lang="ts">
// 工作面板: the right-hand half of a topic — four平级 tabs, 文档 / 现场 / 改动 /
// 预览. It replaces the old 「文档 + 五个按需滑出的抽屉」 (预览/Git/现场/文件/资源):
// the drawers' float/pinned duality, their own width slider and the scrim are
// gone, and 资源 is no longer a panel at all — its numbers live in the topic
// header's usage popover.
//
// This component owns exactly two things, and deliberately nothing else:
//
//   1. WHICH tab is on screen, and WHICH tabs exist at all. Everything a tab
//      renders or fetches belongs to that tab's own SFC, so the four follow-up
//      cards each edit one file.
//   2. SIGNALS — facts about a tab that have to be right while that tab is
//      CLOSED (预览 has new content; how many files 改动 would show). They are
//      here rather than in the tabs because a closed component reports nothing.
//
// The one cross-tab wire is `open-file`: a <&path> chip in the doc (or in the
// chat, via `openFile`) selects the 改动 tab and opens that file there.
import type { PreviewInfo, Topic } from '../cx_types'
import type { TopicPhase } from '../lib/topicState'

import { computed, nextTick, ref, watch } from 'vue'

import { getPreview, getTopicWorkSummary } from '../api'

import PanelChanges from './panels/PanelChanges.vue'
import PanelDoc from './panels/PanelDoc.vue'
import PanelPreview from './panels/PanelPreview.vue'
import PanelSite from './panels/PanelSite.vue'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // Bumped by the parent on AI activity (turn-done / update_doc tool) so 文档
    // reloads the doc 芝士 just wrote. See TopicView activityTick.
    activityTick: number
    // 施工现场 inputs. Only 现场 reads them; they are passed straight through.
    worklog?: { label: string; text: string; platform?: boolean }[]
    working?: boolean
    workingSince?: number | null
    // Project topics (A2): 文档 resolves live-ref badges and <#id> chips with it.
    topicList?: Topic[]
    // Which tab the URL asks for (`?tab=`). The address is the page's business,
    // so TopicView owns it and this component only reports its own moves — that
    // keeps the panel mountable without a router, which is how its four suites
    // exercise it. An unknown or absent value leaves the choice here.
    tab?: string
    // 话题此刻处在哪一段. Only used to pick which tab a topic OPENS on, and only
    // when the address named none — after that it is the reader's choice.
    phase?: TopicPhase
  }>(),
  {
    worklog: () => [],
    working: false,
    workingSince: null,
    topicList: () => [],
    tab: undefined,
    phase: undefined,
  }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'update:tab', key: string): void
}>()

type TabKey = 'doc' | 'site' | 'changes' | 'preview'
interface TabDef {
  key: TabKey
  label: string
  icon: string
}
const ALL_TABS: TabDef[] = [
  { key: 'doc', label: '文档', icon: 'mdi-file-document-outline' },
  { key: 'site', label: '现场', icon: 'mdi-hammer-wrench' },
  { key: 'changes', label: '改动', icon: 'mdi-source-branch' },
  { key: 'preview', label: '预览', icon: 'mdi-eye-outline' },
]
const active = ref<TabKey>('doc')

/** The URL's answer, if it names a tab that exists. */
function tabFromUrl(): TabKey | null {
  const asked = props.tab
  return ALL_TABS.some((t) => t.key === asked) ? (asked as TabKey) : null
}

// Every move the panel makes goes through here, so the address always says what
// is on screen — 「你来看一眼这个 diff」的链接成立的前提就是这个。
function setTab(key: TabKey) {
  settled.value = true
  active.value = key
  if (key === 'changes') markChangesSeen()
  emit('update:tab', key)
}

// ---- 开在哪个 tab 上 (规则 3) ----
// Opening a topic is the one moment choosing a tab is not snatching the view,
// so it is the one moment the panel gets to choose: 芝士 干着活的时候你多半是来
// 看它在干什么的，卡等你验收的时候你是来看它干了什么的。
//
// `settled` is what keeps it to that moment. The phase arrives asynchronously —
// the accept card has to load before anyone knows a card is pending, and until
// it has the prop is undefined rather than 「没有卡」 — so this cannot run on the
// topic switch itself. It runs on the first phase this topic reports, and never
// again unless another topic is opened.
const settled = ref(false)

function tabForPhase(phase: TopicPhase): TabKey {
  if (phase === 'working') return 'site'
  if (phase === 'reviewing' || phase === 'delivering') return 'changes'
  return 'doc'
}

// Back / forward, or someone pasting a link into the open topic.
watch(
  () => props.tab,
  () => {
    const asked = tabFromUrl()
    if (asked && asked !== active.value) active.value = asked
  }
)

// A tab mounts the first time it is selected and then stays mounted — which is
// what the drawer effectively did with its state (openPath, expanded folders,
// the transcript all survived a close/open). 文档 is mounted from the start
// because it is the default tab and its editor is expensive to rebuild.
const mounted = ref<Set<TabKey>>(new Set<TabKey>(['doc']))
watch(active, (k) => {
  if (!mounted.value.has(k)) mounted.value = new Set(mounted.value).add(k)
})

const docRef = ref<InstanceType<typeof PanelDoc> | null>(null)
const changesRef = ref<InstanceType<typeof PanelChanges> | null>(null)

const topicId = computed(() => props.topic?.id ?? null)
const projectId = computed(() => props.topic?.project_id ?? null)

// A turn just ended: that is the moment 芝士's commits, its working tree and
// whatever it pointed the preview at actually changed. One tick, so each tab
// decides for itself what to re-fetch — no tab needs to watch `working`.
const refreshTick = ref(0)
watch(
  () => props.working,
  (now, before) => {
    if (!before || now) return
    refreshTick.value += 1
    void pollPreviewPointer()
    void pollWorkSummary()
  }
)

// ---- 「有新内容」 on the 预览 tab. An artifact is deliberately NOT a chat
// message (it is a pointer, not something 芝士 said), and the tab is not the one
// you are on — so 芝士 could produce something worth looking at and the only way
// to find out was to click on a hunch. These two ids are the whole mechanism:
// what the server currently points at, and what this reader has already had on
// screen. ----
const previewLatest = ref<string | null>(null)
const previewSeen = ref<string | null>(null)
const previewHasNew = computed(() => !!previewLatest.value && previewLatest.value !== previewSeen.value)

function markPreviewSeen(id?: string | null) {
  previewLatest.value = id ?? null
  previewSeen.value = id ?? null
}

// Fetch the POINTER only (no file read, no cookie priming) so the dot can appear
// while 预览 is not the open tab. Cheap enough to run on every turn boundary.
async function pollPreviewPointer(opts: { seen?: boolean } = {}) {
  const tid = props.topic?.id
  if (!tid) return
  let art: PreviewInfo | null = null
  try {
    art = await getPreview(tid)
  } catch {
    // A failed poll is not a state — leave the dot as it was. The real load
    // reports errors; this one only ever adds a hint.
    return
  }
  if (props.topic?.id !== tid) return
  const id = art?.artifact_id ?? null
  // Opening a topic must not greet the reader with a dot for something that was
  // already there before they arrived, and a poll while 预览 is open is looking
  // at it.
  if (opts.seen || active.value === 'preview') markPreviewSeen(id)
  else previewLatest.value = id
}

// ---- 能力: a tab exists only where the thing it shows exists ----
// 文档 is every topic's — it is the topic's state, and a topic always has one.
// The other three are about a workspace 芝士 worked in, and asking for one of
// them on a topic that never ran used to yield a tab whose whole content was a
// sentence explaining there was nothing there. The kind of the topic does NOT
// decide this: the backend gives every topic a worktree and a branch, so 房间型
// vs 任务型 would have been a guess about capability rather than a reading of it.
const summary = ref<{ changedFiles: string[]; hasRun: boolean }>({ changedFiles: [], hasRun: false })

async function pollWorkSummary(opts: { seen?: boolean } = {}) {
  const tid = props.topic?.id
  const pid = props.topic?.project_id
  if (!tid || !pid) return
  let next: { changed_files: string[]; has_run: boolean }
  try {
    next = await getTopicWorkSummary(pid, tid)
  } catch {
    // A failed poll is not a state. Leaving the tab bar as it was beats making
    // tabs disappear because one request lost a race with a redeploy.
    return
  }
  if (props.topic?.id !== tid) return
  summary.value = { changedFiles: next.changed_files, hasRun: next.has_run }
  // Arriving at a topic that already had changes is not news, exactly as it is
  // not news for the preview — the hint means 「这一轮干出来的」.
  if (opts.seen || active.value === 'changes') markChangesSeen()
}

// ---- 「改动变了」 on the 改动 tab, same shape as the preview's dot ----
// The count is the size of the review surface and is always worth showing; what
// needs a colour is 「这些改动是你上次看过之后才有的」. 芝士 works for minutes at a
// time and the reader is usually on 文档 while it does, so without this the only
// way to learn a turn produced anything was to click and compare.
const changesKey = computed(() => summary.value.changedFiles.join('\n'))
const changesSeen = ref<string>('')
const changesHasNew = computed(() => !!changesKey.value && changesKey.value !== changesSeen.value)
function markChangesSeen() {
  changesSeen.value = changesKey.value
}

function tabIsOffered(key: TabKey): boolean {
  // The tab you are ON never disappears from under you. A topic whose changes
  // just merged, or whose preview 芝士 retracted, would otherwise close the
  // thing you were reading — the same rule as 「信号上 Tab，不抢占视图」.
  if (key === active.value) return true
  if (key === 'doc') return true
  // 现场 is where 芝士 works: it is there once the topic has run, and from the
  // first moment of the first turn (before the session id is captured).
  if (key === 'site') return summary.value.hasRun || props.working
  if (key === 'changes') return summary.value.changedFiles.length > 0
  return !!previewLatest.value
}

const tabs = computed(() => ALL_TABS.filter((t) => tabIsOffered(t.key)))

/** What the signal on a tab means, for people who reach it by hover or reader. */
function tabTitle(t: TabDef): string {
  if (t.key === 'site' && props.working) return `${t.label}（芝士正在工作）`
  if (t.key === 'preview' && previewHasNew.value) return `${t.label}（有新内容）`
  if (t.key === 'changes' && summary.value.changedFiles.length) {
    const n = summary.value.changedFiles.length
    return changesHasNew.value ? `${t.label}（${n} 个文件，有新改动）` : `${t.label}（${n} 个文件）`
  }
  return t.label
}
// 房间型话题（谁也没在里面干过活）就只剩文档一个 tab —— 一条只有一个选项的
// tab 栏教不了任何东西，只是一条占着 33px 的横线。
const showTabBar = computed(() => tabs.value.length > 1)

// Topic switch: the address decides, 文档 when it says nothing. Baseline the dot
// against whatever this topic already had, so opening a topic — including
// straight onto 预览 from someone's link — never greets you with a hint for work
// that was there before you arrived.
watch(
  () => props.topic?.id,
  (id) => {
    active.value = tabFromUrl() ?? 'doc'
    // 「URL 里显式带 ?tab= 时以 URL 为准」: an address that names a tab has already
    // decided, so the phase does not get to.
    settled.value = !!tabFromUrl()
    markPreviewSeen(null)
    summary.value = { changedFiles: [], hasRun: false }
    changesSeen.value = ''
    if (id) {
      void pollPreviewPointer({ seen: true })
      void pollWorkSummary({ seen: true })
    }
  },
  { immediate: true }
)

// Declared after the topic watcher on purpose: both fire immediately on mount,
// in declaration order, and this one must see the `settled` that watcher sets.
watch(
  () => props.phase,
  (phase) => {
    if (settled.value || !phase) return
    const want = tabForPhase(phase)
    // No capability check: a topic reporting 待验收 has a card, and a card is a
    // diff. Requiring the summary to have landed first would make the choice a
    // race between two requests fired at the same moment.
    if (want === active.value) settled.value = true
    else setTab(want)
  },
  { immediate: true }
)

// ---- The panel's outward API (TopicView holds a ref) ----
function pulse() {
  setTab('doc')
  void nextTick(() => docRef.value?.pulse())
}
function highlightTurn(turnId: string) {
  setTab('doc')
  void nextTick(() => docRef.value?.highlightTurn(turnId))
}
async function openFile(path: string) {
  setTab('changes')
  await nextTick()
  await changesRef.value?.openFile(path)
}
defineExpose({ pulse, highlightTurn, openFile })
</script>

<template>
  <div class="work-panel">
    <div v-if="!topic" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-file-document-outline</v-icon>
        <div>选择一个话题查看文档</div>
      </div>
    </div>

    <template v-else>
      <div v-if="showTabBar" class="tabbar" role="tablist">
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          role="tab"
          class="tabbar__tab"
          :class="{ 'tabbar__tab--on': active === t.key }"
          :aria-selected="active === t.key"
          :title="tabTitle(t)"
          @click="setTab(t.key)"
        >
          <v-icon size="16">{{ t.icon }}</v-icon>
          {{ t.label }}
          <!-- 信号上 Tab，不抢占视图: 芝士 works for minutes at a time and the
               reader is usually somewhere else while it does, so what it
               produced has to be visible from the tab it produced it on. None
               of these ever selects a tab for you. -->
          <span v-if="t.key === 'site' && working" class="tabbar__pulse" />
          <!-- A dot, not a count: there is only ever one current preview, so a
               number would be noise. -->
          <span v-if="t.key === 'preview' && previewHasNew" class="tabbar__dot" />
          <!-- 改动 is the opposite: how much there is to review is the useful
               part, so the count carries the signal and turns amber when it is
               work you have not looked at yet. -->
          <span
            v-if="t.key === 'changes' && summary.changedFiles.length"
            class="tabbar__count"
            :class="{ 'tabbar__count--new': changesHasNew }"
            >{{ summary.changedFiles.length }}</span
          >
        </button>
      </div>

      <div class="tabbody">
        <PanelDoc
          v-show="active === 'doc'"
          ref="docRef"
          :topic="topic"
          :activity-tick="activityTick"
          :topic-list="topicList"
          @open-topic="emit('open-topic', $event)"
          @mention-click="emit('mention-click', $event)"
          @open-file="openFile"
        />
        <PanelSite
          v-if="mounted.has('site')"
          v-show="active === 'site'"
          :topic="topic"
          :worklog="worklog"
          :working="working"
          :working-since="workingSince"
          :active="active === 'site'"
        />
        <PanelChanges
          v-if="mounted.has('changes')"
          v-show="active === 'changes'"
          ref="changesRef"
          :topic-id="topicId"
          :project-id="projectId"
          :active="active === 'changes'"
          :refresh-tick="refreshTick"
        />
        <PanelPreview
          v-if="mounted.has('preview')"
          v-show="active === 'preview'"
          :topic-id="topicId"
          :project-id="projectId"
          :active="active === 'preview'"
          :refresh-tick="refreshTick"
          @loaded="markPreviewSeen"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.work-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  background: var(--surface);
}
.tabbar {
  display: flex;
  flex: 0 0 auto;
  align-items: stretch;
  gap: 2px;
  padding: 0 6px;
  border-bottom: 1px solid var(--line);
}
.tabbar__tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  cursor: pointer;
}
.tabbar__tab:hover {
  color: var(--ink);
}
/* 选中态: ink + 一条下边线。琥珀只留给唯一主操作、导航选中态和品牌标，工作面板的
   tab 不是导航，所以用中性墨色。 */
.tabbar__tab--on {
  color: var(--ink);
  font-weight: 600;
}
.tabbar__tab--on::after {
  content: '';
  position: absolute;
  inset: auto 8px -1px 8px;
  height: 2px;
  background: var(--ink);
}
/* 有新内容 —— 琥珀在这条 tab 栏里只给「有东西等你看」，不给选中态。 */
.tabbar__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}
/* 芝士正在这个 tab 后面干活。呼吸而不是常亮：常亮说的是「有个东西」，呼吸说的
   是「正在发生」，而现场这一片的全部意义就是后者。 */
.tabbar__pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
  animation: tabbar-breathe 1.6s ease-in-out infinite;
}
@keyframes tabbar-breathe {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
@media (prefers-reduced-motion: reduce) {
  .tabbar__pulse {
    animation: none;
  }
}
/* 改动的规模。默认是中性的事实，只有「你还没看过的那些」才配琥珀。 */
.tabbar__count {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--faint);
}
.tabbar__count--new {
  color: var(--accent);
}
.tabbody {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
</style>
