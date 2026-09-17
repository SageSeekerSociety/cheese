<template>
  <div>
    <!-- 赛题状态提示 -->
    <v-alert
      v-if="taskData?.approved === 'DISAPPROVED' && isSelfTask"
      type="error"
      class="mb-4"
      rounded="lg"
      :title="t('tasks.status.rejected')"
      :text="t('tasks.detail.overview.rejectedText')"
    >
      <template #text>
        <div class="mt-2">
          <div class="font-weight-medium">{{ t('tasks.detail.overview.rejectReason') }}</div>
          <div>{{ taskData.rejectReason }}</div>
        </div>
      </template>
    </v-alert>

    <!-- 个人任务参与限制提示 -->
    <v-alert
      v-if="
        taskData?.submitterType === 'USER' &&
        !canUserJoin &&
        !taskData?.joined &&
        userReasons.length > 0 &&
        !isDeadlinePassed
      "
      type="warning"
      class="mb-4"
      rounded="lg"
      :title="t('tasks.detail.overview.cannotJoinTitle')"
    >
      <template #text>
        <div class="mt-2">
          <div class="font-weight-medium">{{ userReasons[0]?.message || t('tasks.detail.overview.cannotJoin') }}</div>

          <!-- 等级不足提示 -->
          <div v-if="userReasons[0]?.code === 'USER_RANK_NOT_HIGH_ENOUGH'" class="mt-2 text-medium-emphasis">
            {{ t('tasks.detail.overview.rankNotHighEnough') }}
          </div>

          <!-- 缺少实名信息提示 -->
          <div v-if="userReasons[0]?.code === 'USER_MISSING_REAL_NAME'" class="mt-2 text-medium-emphasis">
            <div class="d-flex align-center gap-2">
              <span>{{ t('tasks.detail.overview.needRealName') }}</span>
              <v-btn color="primary" variant="tonal" size="small" :to="{ name: 'UserSettingsRealName' }">
                {{ t('tasks.detail.overview.goFillIn') }}
                <v-icon end>mdi-arrow-right</v-icon>
              </v-btn>
            </div>
          </div>

          <!-- 人数已满提示 -->
          <div v-if="userReasons[0]?.code === 'PARTICIPANT_LIMIT_REACHED'" class="mt-2 text-medium-emphasis">
            {{ t('tasks.detail.overview.participantLimitReached') }}
          </div>
        </div>
      </template>
    </v-alert>

    <!-- 小队任务参与限制提示 -->
    <v-alert
      v-if="taskData?.submitterType === 'TEAM' && !canUserJoin && !taskData?.joined && !isDeadlinePassed"
      type="warning"
      class="mb-4"
      rounded="lg"
      :title="t('tasks.detail.overview.teamNotEligibleTitle')"
    >
      <template #text>
        <div class="mt-2">
          <!-- 没有小队的情况 -->
          <div v-if="noTeams" class="font-weight-medium">
            {{ t('tasks.detail.overview.needTeam') }}
            <div class="mt-2 d-flex align-center">
              <v-btn color="primary" variant="tonal" size="small" :to="{ name: 'HomeTeamsMine' }">
                {{ t('tasks.detail.overview.manageMyTeams') }}
                <v-icon end>mdi-arrow-right</v-icon>
              </v-btn>
            </div>
          </div>

          <!-- 有小队但都不符合条件的情况 -->
          <div v-else-if="hasTeamsButNoneEligible" class="font-weight-medium">
            {{ t('tasks.detail.overview.teamsNoneEligible', { count: teamCount }) }}

            <v-expansion-panels variant="accordion" class="mt-3">
              <v-expansion-panel v-for="teamEligibility in teamEligibilityList" :key="teamEligibility.team.id">
                <v-expansion-panel-title class="py-2">
                  <div class="d-flex align-center">
                    <v-avatar size="24" class="mr-2">
                      <v-img
                        v-if="teamEligibility.team.avatarId"
                        :src="getAvatarUrl(teamEligibility.team.avatarId)"
                        :alt="t('tasks.detail.overview.teamAvatar')"
                      ></v-img>
                      <v-icon v-else>mdi-account-group</v-icon>
                    </v-avatar>
                    <span>{{ teamEligibility.team.name }}</span>
                  </div>
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                  <div v-for="(reason, index) in teamEligibility.eligibility.reasons" :key="index" class="mb-2">
                    <div class="font-weight-medium">
                      <v-icon color="warning" size="small" class="mr-1">mdi-alert-circle</v-icon>
                      {{ reason.message }}
                    </div>

                    <!-- 团队人数不满足要求 -->
                    <div v-if="reason.code === 'TEAM_SIZE_MIN_NOT_MET'" class="mt-1 text-medium-emphasis">
                      {{ t('tasks.detail.overview.teamTooSmall', { count: taskData.minTeamSize }) }}
                    </div>
                    <div v-if="reason.code === 'TEAM_SIZE_MAX_EXCEEDED'" class="mt-1 text-medium-emphasis">
                      {{ t('tasks.detail.overview.teamTooBig', { count: taskData.maxTeamSize }) }}
                    </div>

                    <!-- 团队成员缺少实名信息 -->
                    <div v-if="reason.code === 'TEAM_MEMBER_MISSING_REAL_NAME'" class="mt-1">
                      <p class="text-medium-emphasis mb-2">
                        {{ t('tasks.detail.overview.memberMissingRealName') }}
                      </p>

                      <v-list
                        v-if="teamEligibility.team.memberRealNameStatus"
                        density="compact"
                        class="bg-surface-light rounded-lg pa-0 mb-2"
                      >
                        <v-list-subheader class="text-caption font-weight-medium">{{
                          t('tasks.detail.overview.notVerifiedMembers')
                        }}</v-list-subheader>
                        <v-list-item
                          v-for="member in teamEligibility.team.memberRealNameStatus.filter((m) => !m.hasRealNameInfo)"
                          :key="member.memberId"
                          density="compact"
                          class="py-1"
                        >
                          <template #prepend>
                            <v-icon size="small" color="error" class="mr-2">mdi-account-alert</v-icon>
                          </template>
                          <v-list-item-title class="text-body-2">{{ member.userName }}</v-list-item-title>
                        </v-list-item>
                      </v-list>
                    </div>

                    <!-- 团队成员等级不足 -->
                    <div v-if="reason.code === 'TEAM_MEMBER_RANK_NOT_HIGH_ENOUGH'" class="mt-1 text-medium-emphasis">
                      {{ t('tasks.detail.overview.memberRankNotHighEnough') }}
                    </div>
                  </div>

                  <v-btn
                    color="primary"
                    variant="tonal"
                    size="small"
                    class="mt-2"
                    :to="{ name: 'TeamsDetailMembers', params: { teamId: teamEligibility.team.id } }"
                  >
                    {{ t('tasks.detail.overview.manageThisTeam') }}
                    <v-icon end>mdi-arrow-right</v-icon>
                  </v-btn>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </div>

          <!-- 非小队原因导致的限制 -->
          <div v-else-if="userReasons.length > 0" class="font-weight-medium">
            {{ userReasons[0]?.message || t('tasks.detail.overview.cannotJoin') }}

            <!-- 根据不同原因显示不同提示 -->
            <div v-if="userReasons[0]?.code === 'USER_RANK_NOT_HIGH_ENOUGH'" class="mt-2 text-medium-emphasis">
              {{ t('tasks.detail.overview.rankNotHighEnough') }}
            </div>

            <div v-if="userReasons[0]?.code === 'PARTICIPANT_LIMIT_REACHED'" class="mt-2 text-medium-emphasis">
              {{ t('tasks.detail.overview.participantLimitReached') }}
            </div>
          </div>
        </div>
      </template>
    </v-alert>

    <v-row>
      <v-col cols="12" md="9" lg="8">
        <v-card flat rounded="lg" class="task-detail-card" border="sm">
          <v-card-item>
            <template #prepend>
              <div class="me-3">
                <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
                  <v-icon color="primary" size="28">mdi-information-outline</v-icon>
                </v-avatar>
              </div>
            </template>
            <v-card-title class="text-h5 ps-0">{{ t('tasks.detail.overview.detailTitle') }}</v-card-title>
          </v-card-item>

          <v-card-text>
            <div class="task-description">
              <TipTapViewer v-if="isTipTapJson" :value="tipTapContent" />
              <div v-else-if="renderedMarkdown" class="markdown-body" v-html="renderedMarkdown" />
              <p v-else class="text-medium-emphasis">{{ t('tasks.detail.overview.noDescription') }}</p>
            </div>
          </v-card-text>
        </v-card>

        <!-- 赛题视频 -->
        <v-card v-if="sanitizedVideoUrl" flat rounded="lg" class="mt-4 task-info-card" border="sm">
          <v-card-item>
            <template #prepend>
              <div class="me-3">
                <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
                  <v-icon color="primary" size="28">mdi-video-outline</v-icon>
                </v-avatar>
              </div>
            </template>
            <v-card-title class="text-h5 ps-0">{{ t('tasks.detail.overview.videoTitle') }}</v-card-title>
          </v-card-item>
          <v-card-text>
            <div class="video-container">
              <iframe
                v-if="videoEmbedUrl"
                :src="videoEmbedUrl"
                frameborder="0"
                allowfullscreen
                style="width: 100%; aspect-ratio: 16/9; border-radius: 8px"
              />
              <div v-else>
                <v-alert type="warning" variant="tonal" density="compact" class="mb-3">
                  {{ t('tasks.detail.overview.videoUnsupported') }}
                </v-alert>
                <div class="d-flex align-center gap-2 text-body-2">
                  <v-icon color="primary" size="small">mdi-open-in-new</v-icon>
                  <a :href="sanitizedVideoUrl" target="_blank" rel="noopener" class="text-truncate">{{
                    sanitizedVideoUrl
                  }}</a>
                </div>
              </div>
            </div>
          </v-card-text>
        </v-card>

        <v-card flat rounded="lg" class="mt-4 task-info-card" border="sm">
          <v-card-item>
            <template #prepend>
              <div class="me-3">
                <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
                  <v-icon color="primary" size="28">mdi-information-outline</v-icon>
                </v-avatar>
              </div>
            </template>
            <v-card-title class="text-h5 ps-0">{{ t('tasks.detail.overview.infoTitle') }}</v-card-title>
          </v-card-item>

          <v-divider class="mx-6"></v-divider>

          <v-card-text class="px-6 py-4">
            <div class="d-flex flex-column gap-3">
              <div class="d-flex justify-space-between align-center">
                <div class="text-subtitle-1">{{ t('tasks.detail.overview.submitType') }}</div>
                <v-chip color="primary" variant="flat">
                  {{
                    taskData?.submitterType === 'USER'
                      ? t('tasks.detail.overview.individualTask')
                      : t('tasks.detail.overview.teamTask')
                  }}
                </v-chip>
              </div>

              <v-divider></v-divider>

              <div class="d-flex justify-space-between align-center">
                <div class="text-subtitle-1">{{ t('tasks.detail.overview.difficulty') }}</div>
                <div v-if="taskData?.space?.name?.includes('eTrip')">
                  <!-- 如果赛题属于 eTrip，则按照初级、中级、高级显示（分别对应 1,2,3） -->
                  <v-chip color="primary" variant="flat">
                    {{
                      taskData?.rank === 1
                        ? t('tasks.form.beginner')
                        : taskData?.rank === 2
                          ? t('tasks.form.intermediate')
                          : t('tasks.form.advanced')
                    }}
                  </v-chip>
                </div>
                <div v-else>
                  <v-rating
                    :model-value="rankStars"
                    color="primary"
                    half-increments
                    readonly
                    density="compact"
                  ></v-rating>
                </div>
              </div>

              <v-divider></v-divider>

              <div class="d-flex justify-space-between align-center">
                <div class="text-subtitle-1">{{ t('tasks.detail.overview.submitCount') }}</div>
                <v-chip :color="taskData?.resubmittable ? 'success' : 'warning'" variant="flat">
                  {{
                    taskData?.resubmittable
                      ? t('tasks.detail.overview.multipleSubmissions')
                      : t('tasks.detail.overview.singleSubmission')
                  }}
                </v-chip>
              </div>

              <v-divider></v-divider>

              <div class="d-flex justify-space-between align-center">
                <div class="text-subtitle-1">
                  {{
                    taskData?.submitterType === 'TEAM'
                      ? t('tasks.detail.overview.teamLimitLabel')
                      : t('tasks.detail.overview.personLimitLabel')
                  }}
                </div>
                <div class="d-flex align-center">
                  <v-chip v-if="taskData?.participantLimit" color="primary" variant="flat">
                    {{
                      taskData?.submitterType === 'TEAM'
                        ? t('tasks.detail.overview.teamLimit', { count: taskData.participantLimit })
                        : t('tasks.detail.overview.personLimit', { count: taskData.participantLimit })
                    }}
                  </v-chip>
                  <span v-else class="text-primary font-weight-medium">{{ t('tasks.detail.overview.noLimit') }}</span>
                </div>
              </div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="3" lg="4">
        <v-card flat rounded="lg" class="task-info-card" border="sm">
          <v-card-item>
            <template #prepend>
              <div class="me-3">
                <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
                  <v-icon color="primary" size="28">mdi-clock-outline</v-icon>
                </v-avatar>
              </div>
            </template>
            <v-card-title class="text-h5 ps-0">{{ t('tasks.detail.overview.timeInfo') }}</v-card-title>
          </v-card-item>

          <v-divider class="mx-6"></v-divider>

          <v-card-text class="px-6 py-4">
            <div class="d-flex flex-column gap-3">
              <div class="d-flex justify-space-between align-center">
                <div class="text-subtitle-1">{{ t('tasks.form.deadline') }}</div>
                <div class="d-flex align-center">
                  <span class="text-primary font-weight-medium">
                    {{ formatTaskDate(taskData?.deadline) }}
                  </span>
                  <v-chip v-if="isDeadlineSoon(taskData?.deadline)" color="error" size="small" class="ms-2">
                    {{ t('tasks.detail.overview.closingSoon') }}
                  </v-chip>
                </div>
              </div>

              <v-divider></v-divider>

              <div class="d-flex justify-space-between align-center">
                <div class="text-subtitle-1">{{ t('tasks.form.defaultDeadline') }}</div>
                <div class="text-primary font-weight-medium">
                  {{ t('tasks.detail.overview.days', { count: taskData?.defaultDeadline || 0 }) }}
                </div>
              </div>

              <div v-if="taskData?.joined && taskUserDeadline" class="mt-2">
                <v-alert type="info" variant="tonal" density="comfortable" rounded="lg">
                  <template #text>
                    <div class="d-flex align-center justify-space-between">
                      <span>{{ t('tasks.detail.overview.yourDeadline') }}</span>
                      <CountdownTimer :deadline="taskUserDeadline" label="" class="text-right" />
                    </div>
                  </template>
                </v-alert>
              </div>
            </div>
          </v-card-text>
        </v-card>
        <!-- 项目：从这道赛题开出来的 2.0 项目（git 仓库 + 根话题 + 芝士）。
             列出已有的，而不是直接给一个会默默再建一个的按钮。 -->
        <v-card flat rounded="lg" class="task-info-card mt-4" border="sm">
          <v-card-item>
            <template #prepend>
              <div class="me-3">
                <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
                  <v-icon color="primary" size="28">mdi-source-branch</v-icon>
                </v-avatar>
              </div>
            </template>
            <v-card-title class="text-h5 ps-0">{{ t('tasks.detail.overview.projectsTitle') }}</v-card-title>
          </v-card-item>

          <v-divider class="mx-6"></v-divider>

          <v-card-text class="px-6 py-4">
            <div v-if="taskProjectsLoading" class="text-medium-emphasis">{{ t('global.loading') }}</div>
            <div v-else-if="taskProjectsError" class="text-medium-emphasis">
              {{ taskProjectsError }}
              <v-btn variant="text" size="small" @click="loadTaskProjects">{{ t('global.retry') }}</v-btn>
            </div>
            <div v-else-if="taskProjects.length" class="d-flex flex-column gap-2">
              <a
                v-for="p in taskProjects"
                :key="p.id"
                :href="`/projects/${p.id}`"
                class="d-flex align-center gap-2 text-decoration-none"
              >
                <v-icon size="18" color="primary">mdi-folder-outline</v-icon>
                <span>{{ p.name }}</span>
              </a>
            </div>
            <div v-else class="text-medium-emphasis">{{ t('tasks.detail.overview.noProjects') }}</div>

            <v-btn color="primary" variant="tonal" rounded="pill" class="mt-4" @click="createProjectFromTask">
              <v-icon start>mdi-plus</v-icon>
              {{ t('tasks.detail.overview.createProject') }}
            </v-btn>
            <ResourceLimitsNotice />
          </v-card-text>
        </v-card>

        <v-card flat rounded="lg" class="gradient-card cursor-pointer mt-4" elevation="0" @click="goToAIAdvice">
          <v-card-text class="pa-6">
            <div class="d-flex align-center gap-4">
              <v-avatar color="primary-lighten-4" size="56" class="elevation-0">
                <v-icon color="primary" size="32">mdi-robot</v-icon>
              </v-avatar>

              <div class="flex-grow-1">
                <div class="text-h5 font-weight-bold d-flex flex-wrap align-center gap-2">
                  <i18n-t keypath="tasks.detail.overview.aiName" scope="global" tag="span">
                    <template #brand><span class="text-primary">Navigator AI</span></template>
                  </i18n-t>
                </div>
                <div class="text-medium-emphasis">{{ t('tasks.detail.overview.aiDescription') }}</div>
              </div>

              <v-btn
                color="primary"
                variant="tonal"
                rounded="pill"
                :to="{ name: 'TasksAIAdvice', params: { spaceId: taskData?.space?.id, taskId: taskData?.id } }"
                class="px-4"
              >
                {{ t('tasks.detail.overview.viewAdvice') }}
                <v-icon end>mdi-arrow-right</v-icon>
              </v-btn>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 赛题详情 -->
  </div>
</template>

<script setup lang="ts">
import type { Project as CheesexProject } from '@/cx_types'
import type { Task } from '@/types'

import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { VBtn } from 'vuetify/components'
import dayjs from 'dayjs'

import { getAvatarUrl } from '@/utils/materials'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

// The cheesex (2.0) client: a project here is a git repo + root topic + 芝士,
// not the 1.0 team-project that shares the word.
import { listProjectsForTask } from '@/api'
import { MarkdownRenderer } from '@/components/chat/services/markdownRenderer'
import ResourceLimitsNotice from '@/components/ResourceLimitsNotice.vue'
import { TaskParticipationInfo } from '@/network/api/tasks/types'
import AccountService from '@/services/account'

/** Markdown 渲染器实例，用于将非 TipTap 格式的赛题描述渲染为 HTML */
const markdownRenderer = new MarkdownRenderer()

const TipTapViewer = defineAsyncComponent(() => import('@/components/common/Editor/TipTapViewer.vue'))
const CountdownTimer = defineAsyncComponent(() => import('@/components/common/CountdownTimer.vue'))

const props = defineProps<{
  taskData: Task | null
  participationInfo: TaskParticipationInfo | null
}>()

const router = useRouter()
const { t } = useI18n()

// 从赛题创建 2.0 项目（git 仓库 + 根话题 + 芝士）。1.0 的「团队项目」是另一种
// 东西，同名不同物，这里要的是前者。
const taskProjects = ref<CheesexProject[]>([])
const taskProjectsLoading = ref(false)
const taskProjectsError = ref('')
const { show: showNewProjectDialog } = useNewProjectDialog()

async function loadTaskProjects() {
  const id = props.taskData?.id
  if (!id) return
  taskProjectsLoading.value = true
  taskProjectsError.value = ''
  try {
    taskProjects.value = (await listProjectsForTask(id)).data
  } catch {
    taskProjects.value = []
    taskProjectsError.value = t('tasks.detail.overview.projectsLoadFailed')
  } finally {
    taskProjectsLoading.value = false
  }
}

function createProjectFromTask() {
  const task = props.taskData
  if (!task) return
  const teams =
    props.participationInfo?.identities.filter((i) => i.type === 'TEAM' && i.approved !== 'DISAPPROVED') ?? []
  showNewProjectDialog(teams.length === 1 ? teams[0].memberId : null, { id: task.id, name: task.name })
}

watch(() => props.taskData?.id, loadTaskProjects, { immediate: true })

const formatTaskDate = (date: number | string | Date | null | undefined) => {
  if (!date) return t('tasks.detail.overview.notSet')
  return dayjs(date).format('YYYY-MM-DD HH:mm')
}

const isDeadlineSoon = (date: number | string | Date | null | undefined) => {
  if (!date) return false
  const deadlineDate = dayjs(date)
  const now = dayjs()
  // 如果截止时间在3天内
  return deadlineDate.diff(now, 'day') <= 3 && deadlineDate.isAfter(now)
}

const isSelfTask = computed(() => {
  return props.taskData?.creator.id === AccountService.user?.id
})

/** 判断赛题描述是否为 TipTap JSON 格式（包含 type: 'doc' 的对象） */
const isTipTapJson = computed(() => {
  const raw = props.taskData?.description ?? ''
  if (!raw) return false
  try {
    const parsed = JSON.parse(raw)
    // TipTap JSON 是对象且包含 type: 'doc'
    return typeof parsed === 'object' && parsed !== null && parsed.type === 'doc'
  } catch {
    return false
  }
})

/** 解析 TipTap JSON 内容，解析失败时返回空文档结构 */
const tipTapContent = computed(() => {
  try {
    return JSON.parse(props.taskData?.description ?? '{}')
  } catch {
    return { type: 'doc', content: [] }
  }
})

/** 将非 TipTap 格式的赛题描述作为 Markdown 渲染为 HTML，TipTap 格式时返回空字符串 */
const renderedMarkdown = computed(() => {
  const raw = props.taskData?.description ?? ''
  if (!raw || isTipTapJson.value) return ''
  return markdownRenderer.render(raw)
})

const rankStars = computed(() => {
  return props.taskData?.rank ? props.taskData.rank : 0
})

/** 校验 videoUrl 是否为安全的 HTTP(S) 协议，防止 javascript:/data: XSS */
const sanitizedVideoUrl = computed(() => {
  const url = props.taskData?.videoUrl
  if (!url) return null
  try {
    const parsed = new URL(url)
    if (parsed.protocol === 'https:') {
      return url
    }
  } catch {
    // invalid URL
  }
  return null
})

const videoEmbedUrl = computed(() => {
  const url = props.taskData?.videoUrl
  if (!url) return null

  // Bilibili: https://www.bilibili.com/video/BVxxxx or https://b23.tv/xxxx
  const bvMatch = url.match(/bilibili\.com\/video\/(BV[\w]+)/)
  if (bvMatch) {
    return `//player.bilibili.com/player.html?bvid=${bvMatch[1]}&autoplay=0`
  }

  return null
})

const taskUserDeadline = computed(() => {
  return props.taskData?.userDeadline
})

const isDeadlinePassed = computed(() => {
  if (!props.taskData?.deadline) return false
  return dayjs(props.taskData.deadline).isBefore(dayjs())
})

const canUserJoin = computed(() => {
  if (!props.taskData) return false

  // 对于个人任务，检查用户是否可以参与
  if (props.taskData.submitterType === 'USER') {
    return !!props.taskData.participationEligibility?.user?.eligible
  } else {
    return !!props.taskData.participationEligibility?.teams?.some((team) => team.eligibility.eligible)
  }
})

const userReasons = computed(() => {
  return props.taskData?.participationEligibility?.user?.reasons || []
})

const teamEligibilityList = computed(() => {
  return props.taskData?.participationEligibility?.teams || []
})

const teamCount = computed(() => {
  return teamEligibilityList.value.length
})

const noTeams = computed(() => {
  return teamCount.value === 0
})

const hasTeamsButNoneEligible = computed(() => {
  return teamCount.value > 0 && !teamEligibilityList.value.some((team) => team.eligibility.eligible)
})

const goToAIAdvice = () => {
  if (props.taskData) {
    router.push({
      name: 'TasksAIAdvice',
      params: { spaceId: props.taskData.space?.id, taskId: props.taskData.id },
    })
  }
}
</script>

<style scoped>
.task-description {
  font-size: 1rem;
  line-height: 1.6;
}

/* Markdown 渲染内容样式 */
.markdown-body :deep(h1) {
  font-size: 1.75rem;
  margin: 1.5rem 0 1rem;
  font-weight: 700;
}
.markdown-body :deep(h2) {
  font-size: 1.5rem;
  margin: 1.25rem 0 0.75rem;
  font-weight: 600;
}
.markdown-body :deep(h3) {
  font-size: 1.25rem;
  margin: 1rem 0 0.5rem;
  font-weight: 600;
}
.markdown-body :deep(p) {
  margin: 0.5rem 0;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 1.5rem;
  margin: 0.5rem 0;
}
.markdown-body :deep(li) {
  margin: 0.25rem 0;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 1rem 0;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid rgba(var(--v-border-color), 1);
  padding: 0.5rem 0.75rem;
  text-align: left;
}
.markdown-body :deep(th) {
  background: rgba(var(--v-theme-primary), 0.06);
  font-weight: 600;
}
.markdown-body :deep(blockquote) {
  border-left: 4px solid rgb(var(--v-theme-primary));
  padding: 0.5rem 1rem;
  margin: 0.75rem 0;
  background: rgba(var(--v-theme-primary), 0.04);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
}
.markdown-body :deep(code) {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  padding: 0.125rem 0.375rem;
  border-radius: var(--radius-sm);
  font-size: 0.9em;
}
.markdown-body :deep(pre) {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
  margin: 0.75rem 0;
}
.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
}
.markdown-body :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: 8px;
  margin: 0.75rem 0;
}
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid rgba(var(--v-border-color), 1);
  margin: 1.5rem 0;
}

.task-detail-card,
.task-info-card {
  transition: all 0.3s ease;
}

.gradient-card {
  background: linear-gradient(135deg, rgba(var(--v-theme-primary), 0.05) 0%, rgba(var(--v-theme-primary), 0.15) 100%);
  border: 1px solid rgba(var(--v-theme-primary), 0.1);
  transition: all 0.3s ease;
}

.gradient-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(var(--v-theme-primary), 0.1) !important;
  border: 1px solid rgba(var(--v-theme-primary), 0.2);
}

.cursor-pointer {
  cursor: pointer;
}
</style>
