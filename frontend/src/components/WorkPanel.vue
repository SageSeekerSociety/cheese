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
  }>(),
  { worklog: () => [], working: false, workingSince: null, topicList: () => [], tab: undefined }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'comment-intent', payload: { anchorId: string | null; quote: string }): void
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
  active.value = key
  emit('update:tab', key)
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

async function pollWorkSummary() {
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
    markPreviewSeen(null)
    summary.value = { changedFiles: [], hasRun: false }
    if (id) {
      void pollPreviewPointer({ seen: true })
      void pollWorkSummary()
    }
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
async function refreshComments() {
  await docRef.value?.refreshComments()
}
defineExpose({ pulse, highlightTurn, openFile, refreshComments })
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
          :title="t.key === 'preview' && previewHasNew ? `${t.label}（有新内容）` : t.label"
          @click="setTab(t.key)"
        >
          <v-icon size="16">{{ t.icon }}</v-icon>
          {{ t.label }}
          <!-- A dot, not a count: there is only ever one current preview, so a
               number would be noise. -->
          <span v-if="t.key === 'preview' && previewHasNew" class="tabbar__dot" />
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
          @comment-intent="emit('comment-intent', $event)"
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
/* 有新内容 —— 唯一允许在这里出现的琥珀。 */
.tabbar__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}
.tabbody {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
</style>
