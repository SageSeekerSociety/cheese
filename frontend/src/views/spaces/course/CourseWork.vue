<template>
  <!--
    `course/assignments` 一条路由两种人看：老师看收作业的队列（作业与验收），学生
    看每一周要做什么（本周任务）。和 CourseHome 一样按 `space.admins` 分叉，不按
    地址 —— 侧栏里两边指的是同一格，只是叫法不同。
  -->
  <Assignments v-if="isTeacher" />
  <StudentWeeks v-else />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'

import Assignments from './Assignments.vue'
import StudentWeeks from './StudentWeeks.vue'

import { isCourseTeacher } from '@/lib/courseNav'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

const { currentSpace: space } = storeToRefs(useSpaceStore())

const isTeacher = computed(() =>
  isCourseTeacher(
    (space.value?.admins ?? []).map((admin) => admin.user.id),
    AccountService._user.value?.id
  )
)
</script>
