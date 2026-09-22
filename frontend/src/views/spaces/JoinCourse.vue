<template>
  <v-container class="fill-height justify-center" fluid>
    <v-card class="pa-6 text-center" max-width="520" width="100%" rounded="lg" flat border>
      <template v-if="errorMessage">
        <v-icon color="warning" size="44" class="mb-3">mdi-link-variant-off</v-icon>
        <h1 class="text-h6 mb-2">{{ t('spaces.joinCourse.failedTitle') }}</h1>
        <p class="text-body-2 text-medium-emphasis">{{ errorMessage }}</p>
        <v-btn class="mt-4" color="primary" variant="flat" @click="start">
          {{ t('spaces.joinCourse.retry') }}
        </v-btn>
      </template>

      <!-- 没有内容也算走到了：他确实已经是成员，只是这门课还没发布东西。 -->
      <template v-else-if="arrived">
        <v-icon color="primary" size="44" class="mb-3">mdi-school-outline</v-icon>
        <h1 class="text-h6 mb-2">{{ t('spaces.joinCourse.joinedTitle') }}</h1>
        <p class="text-body-2 text-medium-emphasis">{{ t('spaces.joinCourse.noProject') }}</p>
        <v-btn class="mt-4" color="primary" variant="flat" @click="enterCourse()">
          {{ t('spaces.joinCourse.enterCourse') }}
        </v-btn>
      </template>

      <template v-else>
        <v-progress-circular indeterminate color="primary" class="mb-4"></v-progress-circular>
        <p class="text-body-1">{{ t('spaces.joinCourse.joining') }}</p>
      </template>
    </v-card>

    <!-- 组队只问一次，问过就记住：不组也能继续，所以这不是拦路虎。 -->
    <v-dialog v-model="teamPrompt" max-width="480" persistent>
      <v-card class="pa-2">
        <v-card-title>{{ t('spaces.joinCourse.teamTitle') }}</v-card-title>
        <v-card-text class="text-body-2">{{ t('spaces.joinCourse.teamBody') }}</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="answerTeam(false)">
            {{ t('spaces.joinCourse.teamLater') }}
          </v-btn>
          <v-btn color="primary" variant="flat" @click="answerTeam(true)">
            {{ t('spaces.joinCourse.teamNow') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { SpacesApi } from '@/network/api/spaces'
import { UserApi } from '@/network/api/users'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const errorMessage = ref('')
const arrived = ref(false)
const teamPrompt = ref(false)
const courseId = ref<number | null>(null)

/**
 * 「问过就记住」记在这一台机器上。
 *
 * 它是一句提示，不是一个状态：后端没有「这个人被问过组队没有」这种事，为它
 * 加一张表或一列是拿存储换一句文案。清掉本地存储最多让他再被问一次。
 */
function teamAskedKey(spaceId: number) {
  return `cheese:course-team-asked:${spaceId}`
}

async function start() {
  errorMessage.value = ''
  arrived.value = false

  // 没登录就先去登录，登录后回到这条链接本身 —— SignIn 认 `?redirect=`，
  // 只接受站内路径，所以这里给 `fullPath` 是安全的。
  try {
    await UserApi.getCurrentUser()
  } catch {
    await router.replace({ name: 'SignIn', query: { redirect: route.fullPath } })
    return
  }

  const code = String(route.params.code ?? '').trim()
  if (!code) {
    errorMessage.value = t('spaces.joinCourse.failed')
    return
  }

  try {
    // 两步都是幂等的：加入重复点不会多扣一次码，「我的项目」是问出来的
    // 不是凭空建的，所以点两次链接不会有两个项目。
    const joined = await SpacesApi.join({ code })
    const spaceId = joined.data.space.id
    courseId.value = spaceId
    const enrolled = await SpacesApi.enroll(spaceId)

    if (enrolled.data.project && !localStorage.getItem(teamAskedKey(spaceId))) {
      teamPrompt.value = true
      return
    }
    if (enrolled.data.project) {
      await enterCourse()
      return
    }
    arrived.value = true
  } catch {
    errorMessage.value = t('spaces.joinCourse.failed')
  }
}

/**
 * 落在课程第一屏。
 *
 * 「课程第一屏」是并行那条任务（课程壳与导航）加的路由；它可能还没合进来，
 * 所以按名字探一下，没有就回到今天题目板的落点 —— 这一页不替它决定课程长
 * 什么样。
 */
async function enterCourse(towards?: 'team') {
  const spaceId = courseId.value
  if (spaceId === null) return
  if (towards === 'team' && router.hasRoute('SpacesCourseTeam')) {
    await router.push({ name: 'SpacesCourseTeam', params: { spaceId } })
    return
  }
  if (router.hasRoute('SpacesCourseHome')) {
    await router.push({ name: 'SpacesCourseHome', params: { spaceId } })
    return
  }
  await router.push(`/spaces/${spaceId}`)
}

async function answerTeam(wantsTeam: boolean) {
  const spaceId = courseId.value
  if (spaceId !== null) {
    localStorage.setItem(teamAskedKey(spaceId), '1')
  }
  teamPrompt.value = false
  await enterCourse(wantsTeam ? 'team' : undefined)
}

onMounted(start)
</script>
