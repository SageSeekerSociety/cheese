<template>
  <!--
    一门课的第一屏。同一条路由，两种人看两种东西：老师看「这门课现在有什么在等
    我」，学生看「我这门课要做什么」。分叉按 `space.admins` 判（后端从
    `space_admin_relation` 发下来的那份名单，#1450 之后它就是「教师」），不按地址。
  -->
  <TeacherOverview v-if="isTeacher" />
  <StudentHome v-else />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'

import StudentHome from './StudentHome.vue'
import TeacherOverview from './TeacherOverview.vue'

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
