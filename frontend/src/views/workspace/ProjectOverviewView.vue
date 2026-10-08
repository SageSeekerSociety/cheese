<script setup lang="ts">
// 项目总览（画面）：进项目落在这一页。它答的是「这个项目怎么样了」——
//
// - 项目总览：人写的那一份文档，只读地显示开头，改它去文档页；
// - 最近进展：最近两周发生了什么，按天排（后端从已有的事实算，`project_progress`）；
// - 谁在做什么：还在进行的任务按负责人分，等你的那几件用暖色写出来；
// - 做出了什么：交出去的东西，和网站。
//
// 「需要我处理」不在这一页另列一份：它只在首页「待办」一处，这里只在行上标出来。
// 数据和去处都由容器 ProjectOverview 给。
import type { ArtifactApi } from '@/components/ArtifactManifest.vue'
import type { RoomTask } from '@/cx_types'
import type { ProgressItem } from '@/types/projectProgress'

import { computed, ref } from 'vue'

import ArtifactManifest from '@/components/ArtifactManifest.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { myPhraseLabel, phraseLabel } from '@/lib/board'
import { relTime } from '@/lib/relTime'
import { taskTitle } from '@/lib/topicState'

const props = defineProps<{
  projectId: string
  projectName: string
  /** 项目总览那份文档现在写着什么；还没读到是 null。 */
  overviewText: string | null
  /** 最近进展；还没读到是 null。 */
  progress: ProgressItem[] | null
  progressFailed: boolean
  /** 项目的任务；还没读到是 null。没读到之前不写「0」也不写「暂无」。 */
  tasks: RoomTask[] | null
  names: Record<string, string>
  avatars: Record<string, string>
  me: string
  artifactApi: ArtifactApi
}>()

const emit = defineEmits<{
  (e: 'edit-overview'): void
  (e: 'open-task', task: { taskId: string; roomId: string }): void
  // 鼠标左键按下去了：任务页的代码是懒加载的，等松开再下就白等这几十毫秒
  //（和侧栏任务行的 `press` 同一条）。
  (e: 'press-task', task: { taskId: string; roomId: string }): void
  (e: 'open-artifact', artifactId: string): void
  (e: 'all-tasks'): void
  (e: 'retry-progress'): void
}>()

const nameOf = (handle: string | null | undefined) => (handle ? props.names[handle] || handle : '')

// ---- 项目总览：只显示开头，长了可以展开 ----
const overviewOpen = ref(false)
const OVERVIEW_CLIP = 600
const overviewLong = computed(() => (props.overviewText ?? '').length > OVERVIEW_CLIP)

// ---- 最近进展：按天分组，先列最近几条 ----
const PROGRESS_FIRST = 8
const progressAll = ref(false)
const progressShown = computed(() => {
  const all = props.progress ?? []
  return progressAll.value ? all : all.slice(0, PROGRESS_FIRST)
})

function dayOf(iso: string): string {
  const at = new Date(iso)
  const today = new Date()
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const days = Math.round((start(today) - start(at)) / 86_400_000)
  if (days === 0) return t('work.overview.today')
  if (days === 1) return t('work.overview.yesterday')
  return t('work.overview.date', { month: at.getMonth() + 1, day: at.getDate() })
}
const progressDays = computed(() => {
  const days: { day: string; items: ProgressItem[] }[] = []
  for (const item of progressShown.value) {
    const day = dayOf(item.at)
    const last = days[days.length - 1]
    if (last && last.day === day) last.items.push(item)
    else days.push({ day, items: [item] })
  }
  return days
})
function progressTitle(item: ProgressItem): string {
  if (item.kind === 'version')
    return t('work.overview.versionOf', { name: item.artifactName, version: item.version ?? 0 })
  return taskTitle({ title: item.taskTitle, title_source: item.taskTitleSource })
}
function openProgress(item: ProgressItem) {
  if (item.kind === 'version' && item.artifactId) emit('open-artifact', item.artifactId)
  else if (item.taskId && item.roomId) emit('open-task', { taskId: item.taskId, roomId: item.roomId })
}

// ---- 谁在做什么：还在进行、没停住的任务，按负责人分；我在最前 ----
const PER_PERSON = 4
const going = computed(() =>
  (props.tasks ?? []).filter((task) => task.status === 'open' && task.presentation.column !== 'done' && !task.stalled)
)
const people = computed(() => {
  const byOwner = new Map<string, RoomTask[]>()
  for (const task of going.value) {
    const owner = task.owner_handle ?? ''
    if (!byOwner.has(owner)) byOwner.set(owner, [])
    byOwner.get(owner)!.push(task)
  }
  const rank = (task: RoomTask) => (task.awaits_me ? 0 : task.presentation.phrase === 'running' ? 1 : 2)
  return [...byOwner.entries()]
    .map(([owner, list]) => ({ owner, tasks: [...list].sort((a, b) => rank(a) - rank(b)) }))
    .sort((a, b) => (a.owner === props.me ? -1 : b.owner === props.me ? 1 : b.tasks.length - a.tasks.length))
})
function stateOf(task: RoomTask): { text: string; tone: 'mine' | 'running' | 'plain' } {
  if (task.awaits_me) return { text: myPhraseLabel(task.presentation.phrase), tone: 'mine' }
  if (task.presentation.phrase === 'running') return { text: phraseLabel('running'), tone: 'running' }
  return { text: phraseLabel(task.presentation.phrase), tone: 'plain' }
}
</script>

<template>
  <AppPage :title="projectName" width="wide">
    <div class="overview">
      <div class="overview__main">
        <!-- 项目总览：人写的那一份。显示文档本身，不替它排成字段。 -->
        <section class="ov-section" data-testid="overview-document">
          <div class="ov-head">
            <h2 class="ov-title">{{ t('work.overview.document') }}</h2>
            <button
              v-if="overviewText?.trim()"
              type="button"
              class="ov-link"
              data-testid="overview-edit"
              @click="emit('edit-overview')"
            >
              {{ t('work.overview.edit') }}
            </button>
          </div>
          <template v-if="overviewText?.trim()">
            <div class="ov-doc" :class="{ 'ov-doc--clipped': overviewLong && !overviewOpen }" data-user-content>
              <MarkdownView class="md-content" :source="overviewText" />
            </div>
            <button
              v-if="overviewLong"
              type="button"
              class="ov-link"
              :aria-expanded="overviewOpen"
              @click="overviewOpen = !overviewOpen"
            >
              {{ overviewOpen ? t('work.overview.collapse') : t('work.overview.expand') }}
            </button>
          </template>
          <div v-else-if="overviewText !== null" class="ov-empty">
            <span class="t-body c-muted">{{ t('work.overview.noDocument') }}</span>
            <button type="button" class="ov-link" data-testid="overview-write" @click="emit('edit-overview')">
              {{ t('work.overview.write') }}
            </button>
          </div>
        </section>

        <!-- 最近进展：最近两周的事，按天排。 -->
        <section class="ov-section" data-testid="overview-progress">
          <div class="ov-head">
            <h2 class="ov-title">{{ t('work.overview.progress') }}</h2>
          </div>
          <BaseLoadError
            v-if="progressFailed"
            :title="t('work.overview.progressFailed')"
            @retry="emit('retry-progress')"
          />
          <p v-else-if="progress?.length === 0" class="ov-empty t-body c-muted">{{ t('work.overview.noProgress') }}</p>
          <template v-else-if="progress">
            <div v-for="group in progressDays" :key="group.day" class="ov-day">
              <h3 class="ov-day__label t-meta c-faint">{{ group.day }}</h3>
              <ul class="ov-events">
                <li v-for="item in group.items" :key="`${item.kind}:${item.taskId ?? item.artifactId}:${item.at}`">
                  <button type="button" class="ov-event" @click="openProgress(item)">
                    <span class="ov-event__kind" :class="`ov-event__kind--${item.kind}`">
                      {{ t(`work.overview.kind.${item.kind}`) }}
                    </span>
                    <span class="ov-event__what t-body">
                      <!-- 写的人起的名字不翻译；版本那一句只有文件名是人写的。 -->
                      <span
                        class="ov-event__title"
                        :data-user-content="item.kind === 'version' ? item.artifactName : ''"
                        >{{ progressTitle(item) }}</span
                      >
                      <span v-if="item.by" class="c-faint">
                        · <span data-user-content>{{ nameOf(item.by) }}</span></span
                      >
                    </span>
                    <span class="ov-event__when t-meta c-faint">{{ relTime(item.at) }}</span>
                  </button>
                </li>
              </ul>
            </div>
            <button
              v-if="progress.length > PROGRESS_FIRST && !progressAll"
              type="button"
              class="ov-link ov-more"
              @click="progressAll = true"
            >
              {{ t('work.overview.earlier') }}
            </button>
          </template>
        </section>
      </div>

      <div class="overview__side">
        <!-- 谁在做什么：还在进行的任务按负责人分，等你的用暖色写出来。 -->
        <section class="ov-section" data-testid="overview-people">
          <div class="ov-head">
            <h2 class="ov-title">{{ t('work.overview.people') }}</h2>
            <button type="button" class="ov-link" data-testid="overview-all-tasks" @click="emit('all-tasks')">
              {{ tasks ? t('work.overview.allTasks', { count: going.length }) : t('navigation.project.tasks') }}
            </button>
          </div>
          <p v-if="tasks && !people.length" class="ov-empty t-body c-muted">{{ t('work.overview.nobody') }}</p>
          <div v-for="person in people" :key="person.owner" class="ov-person">
            <div class="ov-person__who t-body">
              <UserAvatar
                :size="20"
                :name="nameOf(person.owner) || t('work.board.noAssignee')"
                :avatar="avatars[person.owner] || ''"
                :seed="person.owner"
              />
              <span class="ov-person__name" :data-user-content="person.owner ? '' : undefined">{{
                nameOf(person.owner) || t('work.board.noAssignee')
              }}</span>
              <span class="c-faint">{{ person.tasks.length }}</span>
            </div>
            <ul class="ov-person__tasks">
              <li v-for="task in person.tasks.slice(0, PER_PERSON)" :key="task.id">
                <button
                  type="button"
                  class="ov-task"
                  @click="emit('open-task', { taskId: task.id, roomId: task.room_id })"
                  @pointerdown="
                    $event.pointerType === 'mouse' &&
                      $event.button === 0 &&
                      emit('press-task', { taskId: task.id, roomId: task.room_id })
                  "
                >
                  <span class="ov-task__title t-body" :data-user-content="task.title || undefined">{{
                    taskTitle(task)
                  }}</span>
                  <span class="ov-task__state" :class="`ov-task__state--${stateOf(task).tone}`">
                    {{ stateOf(task).text }}
                  </span>
                </button>
              </li>
              <li v-if="person.tasks.length > PER_PERSON">
                <button type="button" class="ov-link ov-person__more" @click="emit('all-tasks')">
                  {{ t('work.overview.moreOf', { count: person.tasks.length - PER_PERSON }) }}
                </button>
              </li>
            </ul>
          </div>
        </section>

        <!-- 做出了什么：交出去的东西，和网站。 -->
        <section class="ov-section" data-testid="overview-made">
          <div class="ov-head">
            <h2 class="ov-title">{{ t('work.board.made') }}</h2>
          </div>
          <ArtifactManifest :project-id="projectId" :api="artifactApi" :names="names" />
        </section>
      </div>
    </div>
  </AppPage>
</template>

<style scoped>
.overview {
  container-type: inline-size;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 48px;
  padding-bottom: 32px;
}
.overview__main,
.overview__side {
  display: flex;
  flex-direction: column;
  gap: 32px;
  min-width: 0;
}
@media (width < 1000px) {
  .overview {
    grid-template-columns: minmax(0, 1fr);
    gap: 32px;
  }
}
.ov-section {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.ov-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-2);
}
.ov-title {
  flex: 1 1 auto;
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  font-weight: 600;
  color: var(--ink);
}
.ov-link {
  position: relative;
  align-self: flex-start;
  padding: 4px 0;
  border: 0;
  background: none;
  color: var(--accent-ink);
  font-family: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.ov-link:hover {
  text-decoration: underline;
}
.ov-link::after {
  /* 字小，点的地方不能也小：往四周扩出 44px 高的可点区域。 */
  content: '';
  position: absolute;
  inset: -12px -8px;
}
.ov-empty {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0;
  padding: 12px 0;
}
.ov-doc {
  position: relative;
  padding-top: 12px;
  color: var(--text);
}
.ov-doc--clipped {
  max-height: 240px;
  overflow: hidden;
}
.ov-doc--clipped::after {
  content: '';
  position: absolute;
  inset: auto 0 0;
  height: 56px;
  background: linear-gradient(transparent, var(--surface));
}
.ov-day__label {
  margin: 0;
  padding: 16px 0 4px;
  font-weight: 400;
}
.ov-events,
.ov-person__tasks {
  margin: 0;
  padding: 0;
  list-style: none;
}
.ov-event {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  width: 100%;
  min-height: 44px;
  padding: 0;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.ov-event:hover {
  background: var(--fill);
}
.ov-event__kind {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.ov-event__kind--accepted,
.ov-event__kind--completed {
  color: var(--ok-ink);
}
.ov-event__kind--returned {
  color: var(--warn-ink);
}
.ov-event__kind--stalled,
.ov-event__kind--closed {
  color: var(--faint);
}
.ov-event__what {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ov-event__title {
  color: var(--ink);
}
.ov-event__when {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.ov-more {
  margin-top: 8px;
}
.ov-person {
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.ov-person__who {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--ink);
}
.ov-person__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ov-task {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 44px;
  padding: 0 0 0 28px;
  border: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.ov-task:hover {
  background: var(--fill);
}
.ov-task__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ov-task__state {
  flex-shrink: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.ov-task__state--mine {
  color: var(--accent-ink);
  font-weight: 600;
}
.ov-task__state--running {
  color: var(--ok-ink);
}
.ov-person__more {
  margin-left: 28px;
}
</style>
