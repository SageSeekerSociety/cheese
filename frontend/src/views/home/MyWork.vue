<script setup lang="ts">
// 登录后的首页：我的工作。
//
// 它回答的是「不进任何项目，一眼看见我手上有什么」。以前这个地址给的是空间列表
// ——通篇是**别人**的空间，没有一个字说他自己的项目从哪儿开，于是每天的第一屏
// 是一屏与自己无关的东西（旧页上那张「从这里开始」的卡就是为这个补的）。
//
// 四样东西一张卡：项目名 / 所属空间 / 最近在发生什么 / 一页纸总结。卡按**壳**分
// 组（我的课 / 我的工作 / 我的项目），组里的卡按最近活动排序。
//
// 一句话说清它和后端的关系：这一页**没有自己的目录**。壳来自项目行
// (`ProjectOut.shell`)，动静来自 `/awaiting-me` 加上每个项目一次 `GET /topics`
// （带着 `active_since` 窗口，见 `ACTIVITY_WINDOW_DAYS`），所属空间来自赛题
// (`Project.external_task_id`)，总结来自项目行自己。少一样就少画一样，绝不自己
// 编一个：编出来的那一个和真的长得一样，而它是错的。
import type { Project } from '@/cx_types'
import type { ActivityPart, SpaceRef, WorkCard, WorkSignal } from '@/lib/myWork'

import { computed, onMounted, ref } from 'vue'
import { useDisplay } from 'vuetify'

import { listAwaitingMe, listTopics } from '@/api'
import { t } from '@/i18n'
import {
  activityParts,
  awaitingByProject,
  joinedSpaces,
  NO_SIGNAL,
  sortCards,
  topicsSignal,
  workGroups,
} from '@/lib/myWork'
import { DEFAULT_SHELL, termParams } from '@/lib/shell'
import { TasksApi } from '@/network/api/tasks'
import { useWorkspaceStore } from '@/stores/workspace'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

defineOptions({ name: 'MyWork' })

/**
 * 「最近」有多近。
 *
 * 窗口不是省流量的小聪明，它是这一页的语义：卡按最近活动排序，而「最近」必须有个
 * 边——没有边的话，一个三个月没动过的项目和一个昨天动过的项目都能给出一个时间，
 * 排序看起来一样好，读的人却分不出哪个是活的。窗口外的一律排最后，卡上写「最近
 * 没有动静」，这句话在那个窗口下是真的。
 *
 * 它同时把每个项目的请求限制在「最近活跃的那些房间」上：一个项目可以有上百个房间，
 * 全量拉回来只为了看有没有在跑，是拿一屏的时间换一个数字。
 */
const ACTIVITY_WINDOW_DAYS = 30

const store = useWorkspaceStore()
const { mdAndUp } = useDisplay()
const { show: showNewProjectDialog } = useNewProjectDialog()

const signals = ref<Record<string, WorkSignal>>({})
const spacesByProject = ref<Record<string, SpaceRef | null>>({})

/** 回不到货的那一格就是 `NO_SIGNAL`——不假装有动静，也不把卡片留成空白。 */
const cards = computed<WorkCard[]>(() =>
  store.projects.map((project) => ({
    project,
    signal: signals.value[project.id] ?? NO_SIGNAL,
    space: spacesByProject.value[project.id] ?? null,
  }))
)
const groups = computed(() => workGroups(cards.value))
/** 顶部那一横排：我加入的空间 = 我的项目所在的那几个空间。 */
const spaces = computed(() => joinedSpaces(sortCards(cards.value)))
/** 清单到货之前不画「一个项目都没有」——那是两件事。 */
const settled = computed(() => store.projectsSettled)

function entryOf(project: Project): string {
  return `/projects/${project.id}`
}

function partIcon(part: ActivityPart): string | null {
  if (part.kind === 'running') return 'mdi-play-circle-outline'
  if (part.kind === 'awaiting') return 'mdi-inbox-arrow-down-outline'
  return null
}

function partText(part: ActivityPart): string {
  if (part.kind === 'running') return t('work.running', { count: part.count })
  if (part.kind === 'awaiting') return t('work.awaiting', { count: part.count })
  if (part.kind === 'activity') return t('work.lastActivity', { time: part.time })
  return t('work.quiet')
}

/** 并行问多少个项目。串行会把 20 个项目排成一条长队，无限并行是打自己的后端。 */
const CONCURRENCY = 4

async function eachProject(projects: readonly Project[], load: (project: Project) => Promise<void>) {
  let next = 0
  const worker = async () => {
    while (next < projects.length) {
      const project = projects[next++]
      await load(project)
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, projects.length) }, worker))
}

async function loadActivity(project: Project) {
  const since = new Date(Date.now() - ACTIVITY_WINDOW_DAYS * 24 * 3600 * 1000).toISOString()
  try {
    const { data } = await listTopics(project.id, { sort: 'last_activity_at', order: 'desc', activeSince: since })
    const window = topicsSignal(data)
    signals.value = {
      ...signals.value,
      [project.id]: { ...window, awaiting: signals.value[project.id]?.awaiting ?? 0 },
    }
  } catch {
    // 一个项目问不到不让整页垮掉：其余项目的动静照常显示，这一张卡说「最近没有
    // 动静」。两种情况（确实没动静 / 这一次没取到）下一步动作是同一个——点进去
    // 看——所以不为它弹一条 4 秒的红条。
  }
}

// 赛题 → 空间：同一个赛题只问一次（从一道赛题建出来的项目共享它）。
const spaceCache = new Map<number, Promise<SpaceRef | null>>()

function spaceOfTask(taskId: number): Promise<SpaceRef | null> {
  const cached = spaceCache.get(taskId)
  if (cached) return cached
  const pending = TasksApi.detail(taskId, { querySpace: true })
    .then(({ data }) => {
      const space = data.task?.space
      // 名字为空就不算答案：画一个没有名字的空间，和画一个错的空间一样糟。
      return space && space.name ? { id: space.id, name: space.name } : null
    })
    // 看不见的赛题（未审批、别人建的、已经删了）没有名字可给。这不是错误，
    // 只是这一张卡少一行。
    .catch(() => null)
  spaceCache.set(taskId, pending)
  return pending
}

async function loadSpace(project: Project) {
  // 不是从赛题建出来的项目没有空间可问——今天那条线上不存在「项目的空间」这个
  // 概念，所以这一格就空着，而不是拿项目所属小队或者别的什么顶上。
  const taskId = project.external_task_id
  if (!taskId) return
  const space = await spaceOfTask(taskId)
  spacesByProject.value = { ...spacesByProject.value, [project.id]: space }
}

async function loadAwaiting() {
  try {
    const { data } = await listAwaitingMe()
    const counts = awaitingByProject(data)
    const next: Record<string, WorkSignal> = { ...signals.value }
    for (const project of store.projects) {
      next[project.id] = { ...(next[project.id] ?? NO_SIGNAL), awaiting: counts[project.id] ?? 0 }
    }
    signals.value = next
  } catch {
    // 「什么在等我」问不到时就不提这一句，其余三样照画。
  }
}

onMounted(async () => {
  // 清单先到：卡片（名字、总结）立刻画出来，动静随后一个个填进去——等全部到齐
  // 再画第一帧，就是拿最慢的那个项目当整页的速度。
  await store.refreshProjects()
  void loadAwaiting()
  void eachProject(store.projects, async (project) => {
    await Promise.all([loadActivity(project), loadSpace(project)])
  })
})
</script>

<template>
  <div class="my-work">
    <div class="my-work__inner">
      <!-- 我加入的空间：横排一行。空间列表降级成这一页的一块，不再是一页。 -->
      <section v-if="spaces.length > 0" class="my-work__spaces">
        <h2 class="t-eyebrow mb-2">{{ t('work.mySpaces') }}</h2>
        <div class="my-work__chips">
          <v-chip
            v-for="space in spaces"
            :key="space.id"
            :to="`/spaces/${space.id}`"
            variant="tonal"
            size="small"
            rounded="lg"
          >
            {{ space.name }}
          </v-chip>
          <v-chip :to="{ name: 'HomeSpaces' }" variant="text" size="small" rounded="lg">
            {{ t('work.allSpaces') }}
          </v-chip>
        </div>
      </section>

      <!-- 一个项目都没有（新账号）：给出唯一有意义的下一步，而不是一屏空白。
           空间那一排还在上面——那是他还没加入任何空间时唯一看得见的东西。 -->
      <v-sheet v-if="settled && groups.length === 0" border rounded="lg" class="my-work__empty pa-4">
        <h2 class="t-title mb-1">{{ t('work.emptyTitle') }}</h2>
        <p class="t-body c-muted mb-3">{{ t('work.emptyBody', { project: termParams(DEFAULT_SHELL).project }) }}</p>
        <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="showNewProjectDialog()">
          {{ t('navigation.newProject', { project: termParams(DEFAULT_SHELL).project }) }}
        </v-btn>
      </v-sheet>

      <p v-else-if="!settled" class="t-body c-muted my-work__loading">{{ t('work.loading') }}</p>

      <!-- 我手上的项目：按壳分组。组名读这个壳的词表（项目 / 工作 / 课程），
           所以服务端加第五个壳时这里不用改。 -->
      <section v-for="group in groups" :key="group.key" class="my-work__group">
        <h2 class="t-title my-work__group-title">{{ t('work.groupTitle', { project: termParams(group.shell).project }) }}</h2>
        <v-row density="comfortable">
          <v-col v-for="card in group.cards" :key="card.project.id" cols="12" md="6" lg="4">
            <v-card :to="entryOf(card.project)" class="my-work__card" variant="outlined" rounded="lg">
              <v-card-item class="pb-1">
                <v-card-title class="my-work__name">{{ card.project.name }}</v-card-title>
                <!-- 所属空间：只有从赛题建出来的项目才有，没有就整行不画。 -->
                <v-card-subtitle v-if="card.space" class="my-work__space">{{ card.space.name }}</v-card-subtitle>
              </v-card-item>
              <v-card-text class="pt-2">
                <!-- 什么在跑 / 什么在等我 / 最近什么时候动过。一行，手机上也是这一行。 -->
                <div class="my-work__activity">
                  <span
                    v-for="part in activityParts(card.signal)"
                    :key="part.kind"
                    class="my-work__part"
                    :class="`my-work__part--${part.kind}`"
                  >
                    <v-icon v-if="partIcon(part)" :icon="partIcon(part)!" size="14" class="me-1" />
                    {{ partText(part) }}
                  </span>
                </div>
                <!-- 一页纸总结只给桌面看：手机上是列表，不是卡片详情。 -->
                <p v-if="mdAndUp && card.project.summary" class="my-work__summary">{{ card.project.summary }}</p>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>
      </section>
    </div>
  </div>
</template>

<style scoped>
.my-work {
  height: 100%;
  overflow-y: auto;
}

.my-work__inner {
  max-width: 1140px;
  margin: 0 auto;
  padding: 24px 16px 96px;
}

.my-work__spaces {
  margin-bottom: 8px;
}

/* 横排：装不下时横向滚，而不是换行把「一行」变成两行。 */
.my-work__chips {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 4px;
  scrollbar-width: thin;
}

.my-work__loading {
  margin-top: 24px;
}

.my-work__empty {
  margin-top: 16px;
  border-color: var(--line);
  background: var(--surface);
}

.my-work__group-title {
  margin: 28px 0 4px;
}

.my-work__card {
  height: 100%;
  border-color: var(--line);
  background: var(--surface);
  transition:
    border-color 0.2s ease,
    background-color 0.2s ease;
}

.my-work__card:hover {
  border-color: var(--accent);
  background: var(--fill);
}

.my-work__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  white-space: normal;
  overflow-wrap: anywhere;
}

.my-work__space {
  font-size: 12.5px;
  color: var(--faint);
}

/* 一行就是一行：长出来就截断。手机上一张卡的高度必须是可预期的。 */
.my-work__activity {
  display: flex;
  align-items: center;
  gap: 10px;
  overflow: hidden;
  font-size: 12.5px;
  color: var(--muted);
  white-space: nowrap;
  text-overflow: ellipsis;
}

.my-work__part {
  display: inline-flex;
  flex: none;
  align-items: center;
}

/* 「等我」是这一行里唯一要人动的东西，所以只有它拿到强调色。 */
.my-work__part--awaiting {
  color: var(--accent-ink);
  font-weight: 500;
}

.my-work__summary {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}
</style>
