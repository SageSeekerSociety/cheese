<template>
  <v-tabs v-model="model" show-arrows color="on-surface" slider-color="primary" bg-color="transparent">
    <v-tab
      :value="'overview'"
      :to="{
        name: routeNames.overview,
        params: { spaceId: taskData.space?.id, taskId: taskData.id },
        query: $route.query,
      }"
      exact
    >
      <v-icon start>mdi-information-outline</v-icon>
      概览
    </v-tab>

    <v-tab
      v-if="isCreator || isAdmin"
      :value="'participants'"
      :to="{
        name: routeNames.participants,
        params: { spaceId: taskData.space?.id, taskId: taskData.id },
        query: $route.query,
      }"
    >
      <v-icon start>mdi-account-group</v-icon>
      参与者
    </v-tab>

    <v-tab
      v-if="taskData.joined && taskData.submittable"
      :value="'submissions'"
      :to="{
        name: routeNames.submissions,
        params: { spaceId: taskData.space?.id, taskId: taskData.id },
        query: $route.query,
      }"
    >
      <v-icon start>mdi-tray-full</v-icon>
      提交记录
    </v-tab>

    <v-tab
      v-if="taskData.joined && taskData.submittable"
      :value="'submit'"
      :to="{
        name: routeNames.submit,
        params: { spaceId: taskData.space?.id, taskId: taskData.id },
        query: $route.query,
      }"
    >
      <v-icon start>mdi-upload</v-icon>
      提交
    </v-tab>

    <v-tab
      :value="'ai-advice'"
      :to="{
        name: routeNames.aiAdvice,
        params: { spaceId: taskData.space?.id, taskId: taskData.id },
        query: $route.query,
      }"
    >
      <v-icon start>mdi-robot</v-icon>
      启星研导
    </v-tab>
  </v-tabs>
</template>

<script setup lang="ts">
import { useTaskRouteNames } from '@/lib/shellRouteNames'
import { Task } from '@/types'

const model = defineModel<string>()

/** 同一条工具栏在两棵树里都出现（老的空间页 / 新的题目板外壳），跳哪儿由所在
 *  的那棵树说了算 —— 见 `shellRouteNames.ts` 顶部。 */
const routeNames = useTaskRouteNames()

defineProps<{
  taskData: Task
  isCreator: boolean
  isAdmin: boolean
}>()
</script>
