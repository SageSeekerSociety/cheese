<script setup lang="ts">
import type { MemoryEntryOut } from '../api'
import type { Block, Topic } from '../cx_types'

import { computed, getCurrentInstance } from 'vue'
import { useRouter } from 'vue-router'

import { useCachedResource } from '@/composables/useCachedResource'

import { deleteMemory, getProject, getProjectWeeklies, getTopic, listMemory } from '../api'
import PanelDoc from '../components/panels/PanelDoc.vue'
import { relTime } from '../lib/relTime'
import { myHandle } from '../me'

import { useCommands } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import AppPage from '@/components/common/AppPage.vue'
import i18n, { t } from '@/i18n'
import { markdown, sanitizeRendered } from '@/lib/markdown'
import { useWorkspaceStore } from '@/stores/workspace'

// 项目级文档 (spec §7.1): 章程 / 周报集 / 记忆 — one address each
// (`/projects/:id/docs/:kind`), inside the project frame. Which document to show
// is a route parameter, not a route NAME: as three separate named routes this
// page could be reached two different ways (a sidebar swap and a full-page push)
// that led to two different places under the same words.
type Kind = 'charter' | 'weeklies' | 'memory'
const KINDS: readonly Kind[] = ['charter', 'weeklies', 'memory']

defineOptions({ name: 'ProjectDocsView' })

const props = defineProps<{
  projectId: string
  kind?: string
}>()

const AUTHOR = myHandle()
const router = useRouter()
// 单测里这一页是孤立渲染的，没有 store：队友名字就用默认的。
const workspace = getCurrentInstance()?.appContext.config.globalProperties.$pinia ? useWorkspaceStore() : null

const kind = computed<Kind>(() => (KINDS.includes(props.kind as Kind) ? (props.kind as Kind) : 'charter'))

// 三种文档的切换住在这一页里，不在侧栏——它们是一份文档的三个面，占不起侧栏
// 三行黄金位。切换仍然是一次 router.push：一 kind 一址的承诺不变，所以每一个
// tab 都能收藏、能分享、刷新回到同一页。
function openKind(next: unknown) {
  const k = String(next) as Kind
  if (k === kind.value) return
  void router.push({ name: 'project-docs', params: { projectId: props.projectId, kind: k } })
}

interface DocsPayload {
  rootTopicId: string | null
  /** 章程就是这个房间的文档，编辑器要的是整个房间。 */
  rootTopic: Topic | null
  weeklies: Block[]
  memoryEntries: MemoryEntryOut[]
}

// 一个 kind 一份缓存：三个 tab 是三份不同的文档，来回点不该各转一次圈。
const { data, loading, error } = useCachedResource(
  () => `docs:${props.projectId}:${kind.value}`,
  async (): Promise<DocsPayload> => {
    const project = await getProject(props.projectId)
    const payload: DocsPayload = {
      rootTopicId: project.root_topic_id ?? null,
      rootTopic: null,
      weeklies: [],
      memoryEntries: [],
    }
    if (kind.value === 'charter' && project.root_topic_id) {
      payload.rootTopic = await getTopic(project.root_topic_id)
    } else if (kind.value === 'memory') {
      payload.memoryEntries = (await listMemory(props.projectId, AUTHOR)).data
    } else if (kind.value === 'weeklies') {
      // 一份周报是一条项目级记录，不是标题里带「周报」两个字的房间 —— 按后者
      // 认，一个叫「周报怎么发」的房间也会出现在这儿。
      payload.weeklies = (await getProjectWeeklies(props.projectId)).data
    }
    return payload
  }
)

const weeklies = computed<Block[]>(() => data.value?.weeklies ?? [])
const memoryEntries = computed<MemoryEntryOut[]>(() => data.value?.memoryEntries ?? [])
const errorMessage = computed<string | null>(() =>
  error.value ? error.value.message || t('project.docs.loadFailed') : null
)

function renderMarkdown(text: string): string {
  return sanitizeRendered(markdown.parse(text, { async: false }) as string)
}

// ---- 章程: the root topic's living doc (改了就等于给芝士下指令). It is that
// room's own doc panel, drawn on a page: the same live document, toolbar,
// selection bubble, `/` menu and comments, so the two never drift apart. ----
const rootTopicId = computed<string | null>(() => data.value?.rootTopicId ?? null)
const rootTopic = computed<Topic | null>(() => data.value?.rootTopic ?? null)

function fmtDate(d: string | null): string {
  if (!d) return ''
  return d.length >= 10 ? d.slice(0, 10) : d
}

// 一份周报讲的那一周。窗口是它的身份：并排摆着的几份周报，是这一行把它们分开的。
//
// 按 UTC 读：窗口是**这段时间的标签**，不是某个时刻。用本地时区渲染的话，同一个
// 窗口在西半球会整体前移一天（8月31日 00:00Z 在 PDT 是 8月30日），而写它的人按
// 日期想事情。生产端把不带时区的日期按 UTC 读（见 `_parse_moment`），这里按 UTC
// 显示，两边就一直是同一个日子。
function fmtDay(iso: unknown): string {
  if (typeof iso !== 'string' || !iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  const sameYear = d.getUTCFullYear() === new Date().getUTCFullYear()
  return new Intl.DateTimeFormat(i18n.global.locale.value, {
    year: sameYear ? undefined : 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(d)
}
function weeklyWindow(w: Block): string {
  const since = fmtDay(w.meta?.since)
  const until = fmtDay(w.meta?.until)
  return since && until ? `${since} – ${until}` : fmtDate(w.created_at)
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
// 周报是芝士在项目房间里写的，页头带人过去。
useCommands(() => {
  const room = rootTopicId.value
  if (!room) return []
  if (kind.value === 'weeklies' && weeklies.value.length > 0)
    return [
      {
        id: 'docs.askInRoom',
        title: t('project.docs.askInRoom'),
        icon: 'mdi-message-arrow-right-outline',
        header: { primary: true },
        to: topicTo(room),
      },
    ]
  return []
})
</script>

<template>
  <!-- 章程是一整篇文档，自己带工具条、自己滚、评论栏停在它旁边：这一页不滚，把高度让给它。 -->
  <AppPage
    :title="t('navigation.project.docs')"
    :width="kind === 'charter' ? 'full' : 'read'"
    :fill="kind === 'charter'"
  >
    <div :class="kind === 'charter' ? 'docs-head docs-head--page' : 'mb-6'">
      <div class="docs-tabs">
        <v-tabs
          :model-value="kind"
          density="compact"
          color="on-surface"
          slider-color="primary"
          @update:model-value="openKind"
        >
          <v-tab v-for="k in KINDS" :key="k" :value="k" class="text-none">{{ t(`project.docs.kind.${k}`) }}</v-tab>
        </v-tabs>
        <!-- 章程的顶栏（在线的人、建议、评论……）画在这一行的右边，不另起一行。 -->
        <div v-if="kind === 'charter'" id="charter-doc-bar" class="docs-tabs__bar" />
      </div>
      <p v-if="kind === 'charter'" class="t-body c-muted mt-2">{{ t('project.docs.charterHint') }}</p>
    </div>

    <div v-if="loading" class="d-flex justify-center py-10">
      <v-progress-circular indeterminate color="primary" />
    </div>
    <v-alert v-if="errorMessage" type="error" density="comfortable" class="mb-4">
      {{ errorMessage }}
    </v-alert>

    <template v-if="!loading && !error">
      <!-- ===== 记忆: what 芝士 remembers, human-prunable ===== -->
      <template v-if="kind === 'memory'">
        <div v-if="memoryEntries.length === 0" class="text-medium-emphasis text-body-2 py-6 text-center">
          <v-icon size="28" class="text-disabled mb-2">mdi-brain</v-icon>
          <div>{{ t('project.docs.memoryEmpty') }}</div>
          <div class="text-caption mt-1">{{ t('project.docs.memoryHint') }}</div>
        </div>
        <v-card v-for="e in memoryEntries" :key="e.id" class="memory-card mb-2" variant="flat">
          <div class="d-flex align-start ga-3 pa-3">
            <v-icon size="16" class="c-muted mt-1">
              {{ e.scope === 'user' ? 'mdi-account-outline' : 'mdi-source-repository' }}
            </v-icon>
            <div class="flex-grow-1">
              <div class="memory-card__content">{{ e.content }}</div>
              <div class="t-meta c-muted mt-1">
                {{ e.scope === 'user' ? t('project.docs.memoryUser') : t('project.docs.memoryProject') }} ·
                {{ relTime(e.created_at) }}
              </div>
            </div>
            <BaseButton
              icon="mdi-delete-outline"
              kind="danger"
              size="sm"
              class="memory-card__del"
              :title="t('project.docs.memoryDelete')"
              @click="removeMemory(e.id)"
            />
          </div>
        </v-card>
      </template>

      <!-- ===== 章程: the project room's own doc panel, on a page ===== -->
      <template v-else-if="kind === 'charter'">
        <PanelDoc
          v-if="rootTopic"
          bare
          bar-to="#charter-doc-bar"
          class="charter-doc"
          :topic="rootTopic"
          :activity-tick="0"
          :agent-name="workspace?.agentName"
          :agent-handle="workspace?.agentHandle"
          :topic-list="workspace?.topics ?? []"
          @open-topic="(id: string) => router.push(topicTo(id))"
        />
        <div v-else class="text-medium-emphasis text-body-2 py-2">{{ t('project.docs.noCharter') }}</div>
      </template>

      <!-- ===== 周报集 ===== -->
      <template v-else>
        <!-- 空态说实话。以前这里写「周报由芝士定期产出」——平台既没有生成器，
               也没有任何定期的东西，那句话是句承诺而不是一句描述。现在周报真的
               由芝士写，所以要说清的是**怎么让它写**，不是它已经在写了。 -->
        <div v-if="weeklies.length === 0" class="text-medium-emphasis text-body-2 py-6 text-center">
          <div>{{ t('project.docs.weekliesEmpty') }}</div>
          <div class="text-caption mt-1">{{ t('project.docs.weekliesHint') }}</div>
          <BaseButton
            v-if="rootTopicId"
            :to="topicTo(rootTopicId)"
            kind="secondary"
            size="sm"
            class="mt-2"
            append-icon="mdi-arrow-right"
          >
            {{ t('project.docs.goToRoom') }}
          </BaseButton>
        </div>
        <!-- 一份周报是一份读的东西，不是一行导航：它有自己的窗口、自己的正文，
               还有「写在哪」。所以整卡摊开。 -->
        <div v-else class="d-flex flex-column ga-3">
          <v-card v-for="w in weeklies" :key="w.id" class="weekly-card">
            <div class="pa-4">
              <div class="d-flex align-center ga-2 mb-2">
                <v-icon size="17" class="c-faint">mdi-calendar-week-outline</v-icon>
                <span class="t-body" style="font-weight: 500">{{ weeklyWindow(w) }}</span>
                <span class="t-meta">{{ t('project.docs.recordedOn', { date: fmtDate(w.created_at) }) }}</span>
                <v-spacer />
                <BaseButton
                  v-if="w.topic_id"
                  :to="topicTo(w.topic_id)"
                  kind="ghost"
                  size="sm"
                  append-icon="mdi-arrow-top-right"
                >
                  {{ t('project.docs.fromTopic') }}
                </BaseButton>
              </div>
              <div class="md-content text-body-2" v-html="renderMarkdown(w.content)" />
            </div>
          </v-card>
        </div>
      </template>
    </template>
  </AppPage>
</template>

<style scoped>
/* 几种文档的切换带。它以前是侧栏里几行常驻的一级导航，占着黄金位养的却是
   二级页面；收成这一条 tab 带之后，侧栏只留一行「项目文档」。 */
.docs-tabs {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
  border-bottom: 1px solid var(--line);
}
.docs-tabs__bar {
  display: flex;
  flex: 1 1 auto;
  justify-content: flex-end;
  min-width: 0;
}

/* 章程：编辑器整页宽、占满剩下的高度、自己滚；页签那一行仍摆在阅读宽度的那一栏里，
   切到周报集、记忆时不跳。 */
.docs-head--page {
  box-sizing: border-box;
  width: 100%;
  max-width: calc(var(--page-w) + 32px);
  margin-inline: auto;
  padding: 24px 16px 8px;
}
.charter-doc {
  flex: 1 1 auto;
  min-height: 0;
}

/* 周报集: 一份周报 = 一个对象，卡片保留（描边来自全局 VCard 默认的
   flat + border=thin，没有阴影）。 */
.weekly-card {
  /* 正文里的长表格/代码块不许冲出 12px 圆角。 */
  overflow: hidden;
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
  transition: opacity var(--dur-quick) var(--ease-standard);
}
.memory-card:hover .memory-card__del {
  opacity: 1;
}
/* 没有 hover 的设备上（手机、平板）等不到它出现，所以常驻。按输入方式判断，不按
   视口宽度，和话题侧栏的行操作同一个判断。 */
@media (hover: none) {
  .memory-card__del {
    opacity: 1;
  }
}
</style>
