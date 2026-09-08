<script setup lang="ts">
import type { MemoryEntryOut } from '../api'
import type { Block, Topic } from '../cx_types'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import DOMPurify from 'dompurify'

import { useCachedResource } from '@/composables/useCachedResource'

import { deleteMemory, getProject, getProjectDecisions, listMemory, listTopics } from '../api'
import DocEditor from '../components/DocEditor.vue'
import { relTime } from '../lib/relTime'
import { myHandle } from '../me'

import { markdown } from '@/lib/markdown'

// 项目级文档 (spec §7.1): 章程 / 决策记录 / 周报集 / 记忆 — one address each
// (`/projects/:id/docs/:kind`), inside the project frame. Which document to show
// is a route parameter, not a route NAME: as three separate named routes this
// page could be reached two different ways (a sidebar swap and a full-page push)
// that led to two different places under the same words.
type Kind = 'charter' | 'decisions' | 'weeklies' | 'memory'
const KINDS: readonly Kind[] = ['charter', 'decisions', 'weeklies', 'memory']

defineOptions({ name: 'ProjectDocsView' })

const props = defineProps<{
  projectId: string
  kind?: string
}>()

const AUTHOR = myHandle()
const router = useRouter()

const kind = computed<Kind>(() => (KINDS.includes(props.kind as Kind) ? (props.kind as Kind) : 'charter'))

// 四种文档的切换住在这一页里，不在侧栏——它们是一份文档的四个面，占不起侧栏
// 四行黄金位。切换仍然是一次 router.push：一 kind 一址的承诺不变，所以每一个
// tab 都能收藏、能分享、刷新回到同一页。
function openKind(next: unknown) {
  const k = String(next) as Kind
  if (k === kind.value) return
  void router.push({ name: 'project-docs', params: { projectId: props.projectId, kind: k } })
}

const TITLES: Record<Kind, string> = {
  charter: '章程',
  decisions: '决策记录',
  weeklies: '周报集',
  memory: '记忆',
}
const OVERLINES: Record<Kind, string> = {
  charter: '项目文档',
  decisions: '项目文档',
  weeklies: '项目文档',
  memory: '芝士记住的事',
}

interface DocsPayload {
  projectName: string
  rootTopicId: string | null
  decisions: Block[]
  weeklies: Topic[]
  memoryEntries: MemoryEntryOut[]
}

// 一个 kind 一份缓存：四个 tab 是四份不同的文档，来回点不该各转一次圈。
const { data, loading, error } = useCachedResource(
  () => `docs:${props.projectId}:${kind.value}`,
  async (): Promise<DocsPayload> => {
    const project = await getProject(props.projectId)
    const payload: DocsPayload = {
      projectName: project.name,
      // DocEditor loads/persists the doc itself once rootTopicId is set.
      rootTopicId: project.root_topic_id ?? null,
      decisions: [],
      weeklies: [],
      memoryEntries: [],
    }
    if (kind.value === 'decisions') {
      payload.decisions = (await getProjectDecisions(props.projectId)).data
    } else if (kind.value === 'memory') {
      payload.memoryEntries = (await listMemory(props.projectId, AUTHOR)).data
    } else if (kind.value === 'weeklies') {
      payload.weeklies = (await listTopics(props.projectId)).data.filter((t) => t.title.includes('周报'))
    }
    return payload
  }
)

const projectName = computed<string>(() => data.value?.projectName ?? '')
const decisions = computed<Block[]>(() => data.value?.decisions ?? [])
const weeklies = computed<Topic[]>(() => data.value?.weeklies ?? [])
const memoryEntries = computed<MemoryEntryOut[]>(() => data.value?.memoryEntries ?? [])
// 章程的保存失败是「刚才那一下没成」，跟「这一页加载不出来」分开报。
const saveError = ref<string | null>(null)
const errorMessage = computed<string | null>(
  () => saveError.value ?? (error.value ? error.value.message || '加载失败' : null)
)

function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(markdown.parse(text, { async: false }) as string)
}

// ---- 章程: the root topic's living doc (改了就等于给芝士下指令). The rich
// editor (DocEditor) owns loading/saving the doc's markdown via the same
// getDoc/putDoc API PanelDoc uses. Like the workspace PanelDoc, it is ALWAYS
// editable and autosaves (debounce + ⌘S + blur) — no 编辑 toggle. Here we only
// mirror the save-status indicator it emits. ----
const rootTopicId = computed<string | null>(() => data.value?.rootTopicId ?? null)
const saving = ref(false)
const savedAt = ref<number | null>(null)
const charterDirty = ref(false)

// 换一个 tab（或换一个项目）等于换一篇文档，上一篇的保存状态不能跟过来。
watch([kind, () => props.projectId], () => {
  saving.value = false
  savedAt.value = null
  charterDirty.value = false
  saveError.value = null
})

function onCharterSaving() {
  saving.value = true
  savedAt.value = null
}
function onCharterSaved() {
  saving.value = false
  charterDirty.value = false
  savedAt.value = Date.now()
}
function onCharterDirty() {
  charterDirty.value = true
  savedAt.value = null
}
function onCharterError(message: string) {
  saving.value = false
  saveError.value = message
}

function fmtDate(d: string | null): string {
  if (!d) return ''
  return d.length >= 10 ? d.slice(0, 10) : d
}

// ---- 记忆 (spec §8.4 记忆可见): entries 芝士 remembered, human-prunable ----
// 删一条要写回缓存里的那份，不然离开这一页再回来它又出现了。
async function removeMemory(id: string) {
  await deleteMemory(id)
  if (data.value) data.value.memoryEntries = data.value.memoryEntries.filter((e) => e.id !== id)
}

// A source-topic link: open that topic in the same project frame.
function topicTo(topicId: string | null | undefined) {
  if (!topicId) return { name: 'workspace-project', params: { projectId: props.projectId } }
  return { name: 'workspace-topic', params: { projectId: props.projectId, topicId } }
}
</script>

<template>
  <div class="docs-page fill-height overflow-y-auto">
    <v-container class="py-6 page-container">
      <div class="mb-4">
        <div class="t-eyebrow mb-1">{{ OVERLINES[kind] }}</div>
        <div class="d-flex align-center flex-wrap ga-3">
          <h1 class="t-page-title" style="font-size: 27px">{{ TITLES[kind] }}</h1>
          <span v-if="projectName" class="t-meta">{{ projectName }}</span>
          <template v-if="kind === 'charter'">
            <v-spacer />
            <v-btn v-if="rootTopicId" :to="topicTo(rootTopicId)" variant="text" size="small" prepend-icon="mdi-history"
              >修改记录</v-btn
            >
            <span v-if="saving" class="t-meta">保存中…</span>
            <span v-else-if="savedAt" class="d-inline-flex align-center ga-1 c-faint" style="font-size: 12px">
              <span class="status-dot status-dot--ok" />已保存
            </span>
            <span v-else-if="charterDirty" class="t-meta">未保存</span>
          </template>
        </div>
        <div v-if="kind === 'charter'" class="t-meta mt-1">改了就等于给芝士下指令</div>
      </div>

      <v-tabs
        :model-value="kind"
        density="compact"
        color="primary"
        class="docs-tabs mb-6"
        @update:model-value="openKind"
      >
        <v-tab v-for="k in KINDS" :key="k" :value="k" class="text-none">{{ TITLES[k] }}</v-tab>
      </v-tabs>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="errorMessage" type="error" density="comfortable" class="mb-4">
        {{ errorMessage }}
      </v-alert>

      <template v-else>
        <!-- ===== 记忆: what 芝士 remembers, human-prunable ===== -->
        <template v-if="kind === 'memory'">
          <div v-if="memoryEntries.length === 0" class="text-medium-emphasis text-body-2 py-6 text-center">
            <v-icon size="28" class="text-disabled mb-2">mdi-brain</v-icon>
            <div>暂无记忆</div>
            <div class="text-caption mt-1">对话里说「记住……」，或它自己判断重要时，会写进这里</div>
          </div>
          <v-card v-for="e in memoryEntries" :key="e.id" class="memory-card mb-2" variant="flat">
            <div class="d-flex align-start ga-3 pa-3">
              <v-icon size="16" class="c-muted mt-1">
                {{ e.scope === 'user' ? 'mdi-account-outline' : 'mdi-source-repository' }}
              </v-icon>
              <div class="flex-grow-1">
                <div class="memory-card__content">{{ e.content }}</div>
                <div class="t-meta c-muted mt-1">
                  {{ e.scope === 'user' ? '个人记忆' : '项目记忆' }} · {{ relTime(e.created_at) }}
                </div>
              </div>
              <v-btn
                icon="mdi-delete-outline"
                size="x-small"
                variant="text"
                class="c-muted memory-card__del"
                title="删除这条记忆"
                @click="removeMemory(e.id)"
              />
            </div>
          </v-card>
        </template>

        <!-- ===== 章程: project root doc, read/edit with the rich tiptap
             editor — the SAME editing experience as the workspace doc panel
             (drag handle, tables, task lists, code highlighting), persisted via
             the same getDoc/putDoc API. ===== -->
        <template v-else-if="kind === 'charter'">
          <!-- 一整篇文档，不是列表里的一个对象 —— 根面是白底之后，把它框进一张
               白卡片只是给白底加了个轮廓。直接铺在页面上。 -->
          <div class="charter-body">
            <DocEditor
              v-if="rootTopicId"
              :topic-id="rootTopicId"
              :editable="true"
              placeholder="芝士还没写章程——它会在你定下项目方向后维护这份文档。你也可以直接在这里写，内容会自动保存。"
              @saving="onCharterSaving"
              @saved="onCharterSaved"
              @dirty="onCharterDirty"
              @error="onCharterError"
            />
            <div v-else class="text-medium-emphasis text-body-2 py-2">这个项目还没有可编辑的章程文档</div>
          </div>
        </template>

        <!-- ===== 决策记录 ===== -->
        <template v-else-if="kind === 'decisions'">
          <div v-if="decisions.length === 0" class="text-medium-emphasis text-body-2 py-6 text-center">
            <div>暂无决策记录</div>
            <div class="text-caption mt-1">芝士在协作中定下关键决策时会记到这里</div>
          </div>
          <div v-else class="d-flex flex-column ga-3">
            <!-- 一条决策是列表里真正可拿起的对象（有自己的日期、正文和「来自
                 话题」入口），所以卡片形态保留。左侧那条 3px 竖条删掉：区块强调
                 不用左条纹，卡片自己的 --line 描边已经把边界说清楚了。 -->
            <v-card v-for="d in decisions" :key="d.id" class="decision-card">
              <div class="pa-4">
                <div class="d-flex align-center ga-2 mb-2">
                  <v-icon size="17" class="c-faint"> mdi-clipboard-text-clock-outline </v-icon>
                  <span class="t-meta">{{ fmtDate(d.created_at) }}</span>
                  <v-spacer />
                  <v-btn
                    v-if="d.topic_id"
                    :to="topicTo(d.topic_id)"
                    size="x-small"
                    variant="text"
                    class="c-muted"
                    append-icon="mdi-arrow-top-right"
                  >
                    来自话题
                  </v-btn>
                </div>
                <div class="md-content text-body-2" v-html="renderMarkdown(d.content)" />
              </div>
            </v-card>
          </div>
        </template>

        <!-- ===== 周报集 ===== -->
        <template v-else>
          <div v-if="weeklies.length === 0" class="text-medium-emphasis text-body-2 py-6 text-center">
            <div>暂无周报</div>
            <div class="text-caption mt-1">周报由芝士定期产出</div>
          </div>
          <!-- 一份周报整卡就是一个链接：没有卡内操作、没有第二层信息，它是导航
               行不是对象卡。改成带发丝线的行列表，和总览页的成员列表同一套语法。 -->
          <div v-else class="weekly-list">
            <router-link v-for="t in weeklies" :key="t.id" class="weekly-row" :to="topicTo(t.id)">
              <v-icon size="18" class="c-faint">mdi-calendar-week-outline</v-icon>
              <div class="flex-grow-1" style="min-width: 0">
                <div class="t-body text-truncate" style="font-weight: 500; color: var(--ink)">
                  {{ t.title }}
                </div>
                <div class="t-meta">{{ fmtDate(t.created_at) }}</div>
              </div>
              <v-icon size="18" class="c-faint">mdi-chevron-right</v-icon>
            </router-link>
          </div>
        </template>
      </template>
    </v-container>
  </div>
</template>

<style scoped>
/* 内容区是侧栏 (--canvas) 上面那张 surface —— 和话题视图、总览同一层关系。 */
.docs-page {
  background: var(--surface);
}

/* 四种文档的切换带。它以前是侧栏里四行常驻的一级导航，占着黄金位养的却是四个
   二级页面；收成这一条 tab 带之后，侧栏只留一行「项目文档」。 */
.docs-tabs {
  border-bottom: 1px solid var(--line);
}

/* 章程: 页面本身就是那张纸。左右不再补内边距 —— DocEditor 自带 56px 的左侧
   拖拽手柄槽，再叠一层会把正文推得离页头更远。 */
.charter-body {
  padding: 4px 0 40px;
}

/* 决策记录: 一条决策 = 一个对象，卡片保留（描边来自全局 VCard 默认的
   flat + border=thin，没有阴影）。 */
.decision-card {
  /* 正文里的长表格/代码块不许冲出 12px 圆角。 */
  overflow: hidden;
}

/* 周报集: 行列表，靠发丝线分隔。列表不自带顶边 —— 上面 .docs-tabs 的底边线就
   是它的顶边，再画一条会在 24px 之内出现两条平行的满宽横线。 */
.weekly-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 8px;
  border-bottom: 1px solid var(--line);
  text-decoration: none;
  color: inherit;
  transition: background 0.12s ease;
}
.weekly-row:hover {
  background: var(--fill);
}
</style>

<style scoped>
.memory-card {
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}
.memory-card__content {
  font-size: 0.9rem;
  line-height: 1.55;
  white-space: pre-wrap;
}
.memory-card__del {
  opacity: 0;
  transition: opacity 0.12s;
}
.memory-card:hover .memory-card__del {
  opacity: 1;
}
</style>
