<template>
  <!--
    学生与分组（老师）。这门课有哪些学生、他们在做什么、谁和谁一组。

    数据来自 `GET /spaces/{id}/course/roster` —— 成员表 + 项目 + 小队三张表在服务
    端合成的一行一行，前端只负责画。**这里没有一样是前端自己算的**：谁算学生
    （成员减去管理员）、谁和谁一组（项目的 teamId），答案都在服务端。

    今天这一屏是**只读**的：把学生放进组这件事，团队那侧只认团队自己的角色
    （`require_permission(TEAM_MEMBERSHIP)`），老师不在学生的组里，所以他改不了别人
    的组。要支持「老师指定分组」，得先在授权那一层加一条主张 —— 那是安全面的改动，
    不是这一屏顺手能带的。屏里那句说明把现状讲给老师听，不摆一个按不动的按钮。
  -->
  <v-sheet flat rounded="lg" class="pa-4">
    <div class="d-flex align-center flex-wrap ga-3 mb-3">
      <h1 class="text-h6">{{ t('spaces.course.people.title') }}</h1>
      <v-chip v-if="!loading" size="small" variant="tonal">
        {{ t('spaces.course.people.count', { n: students.length }) }}
      </v-chip>
    </div>

    <v-alert v-if="isPending" type="info" variant="tonal" density="comfortable" class="mb-4">
      {{ t('spaces.course.pending.body') }}
    </v-alert>

    <div v-if="loading" class="text-center pa-4">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <template v-else>
      <div class="text-subtitle-2 mb-2">{{ t('spaces.course.people.groups') }}</div>

      <v-row v-if="teams.length" dense>
        <v-col v-for="team in teams" :key="team.id" cols="12" md="6">
          <v-sheet flat rounded="lg" class="section pa-4">
            <div class="d-flex align-center flex-wrap ga-2 mb-3">
              <v-icon icon="mdi-account-multiple-outline" size="18"></v-icon>
              <span class="font-weight-medium">{{ team.name }}</span>
              <v-chip size="x-small" variant="tonal">
                {{ t('spaces.course.people.memberCount', { n: team.members.length }) }}
              </v-chip>
            </div>
            <div class="d-flex flex-wrap ga-3">
              <div v-for="person in team.members" :key="person.id" class="d-flex align-center ga-2">
                <v-avatar size="28" color="surface-variant">
                  <v-img :src="getAvatarUrl(person.avatarId ?? undefined)" />
                </v-avatar>
                <span class="text-body-2">{{ displayName(person) }}</span>
              </div>
            </div>
            <p v-if="!team.members.length" class="text-medium-emphasis mb-0">
              {{ t('spaces.course.people.emptyGroup') }}
            </p>
          </v-sheet>
        </v-col>
      </v-row>

      <v-sheet v-else flat rounded="lg" class="section pa-6 text-center mb-4">
        <p class="text-medium-emphasis mb-0">{{ t('spaces.course.people.noGroups') }}</p>
      </v-sheet>

      <div class="text-subtitle-2 mb-2 mt-4">{{ t('spaces.course.people.ungrouped') }}</div>

      <v-sheet v-if="ungrouped.length" flat rounded="lg" class="section pa-2">
        <v-list density="comfortable" class="pa-0">
          <v-list-item v-for="student in ungrouped" :key="student.user.id">
            <template #prepend>
              <v-avatar size="32" color="surface-variant" class="mr-3">
                <v-img :src="getAvatarUrl(student.user.avatarId ?? undefined)" />
              </v-avatar>
            </template>
            <v-list-item-title>{{ displayName(student.user) }}</v-list-item-title>
            <v-list-item-subtitle>{{ projectLabel(student) }}</v-list-item-subtitle>
          </v-list-item>
        </v-list>
      </v-sheet>

      <v-sheet v-else flat rounded="lg" class="section pa-6 text-center">
        <p class="text-medium-emphasis mb-0">{{ t('spaces.course.people.allGrouped') }}</p>
      </v-sheet>

      <v-alert type="info" variant="tonal" density="comfortable" class="mt-4">
        {{ t('spaces.course.people.howGroupingWorks') }}
      </v-alert>
    </template>
  </v-sheet>
</template>

<script setup lang="ts">
import type { CourseRosterPerson, CourseRosterStudent } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { getAvatarUrl } from '@/utils/materials'

import { SpacesApi } from '@/network/api/spaces'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const { currentSpace: space } = storeToRefs(useSpaceStore())

const spaceId = String(route.params.spaceId)
const loading = ref(true)
const students = ref<CourseRosterStudent[]>([])
const teams = ref<{ id: number; name: string; members: CourseRosterPerson[] }[]>([])

const isPending = computed(() => space.value?.reviewStatus === 'PENDING')

const ungrouped = computed(() => students.value.filter((student) => student.teamIds.length === 0))

function displayName(person: CourseRosterPerson): string {
  return person.nickname || person.username
}

function projectLabel(student: CourseRosterStudent): string {
  if (!student.projects.length) return t('spaces.course.people.noProject')
  return student.projects.map((project) => project.name).join('、')
}

onMounted(async () => {
  const id = Number(spaceId)
  if (!Number.isFinite(id) || id <= 0) {
    loading.value = false
    return
  }
  try {
    const { data } = await SpacesApi.getCourseRoster(id)
    students.value = data.students
    teams.value = data.teams
  } catch {
    // 不是管理员，或者这块板还没过审（子资源 404）：这一屏就是空的。
    students.value = []
    teams.value = []
  }
  loading.value = false
})
</script>

<style scoped lang="scss">
.section {
  border: 1px solid var(--line);
}
</style>
