<script setup lang="ts">
// 题目详情挂进新外壳的那一层。
//
// 详情**一行都没重写**：底下就是老树里那一页（`views/tasks/Detail.vue` 和它下面
// 那五格）。按 `submissionSchema` 出表单的提交、逐版评审、启星研导、B 站视频、
// 第四批的题目附件全在原处 —— 这一批要的是「在新外壳里也能走到」，不是重做一遍。
//
// 这一层补的是页面**外面**的两件事：
//
// 1. **路由名那一组 provide 下去**。老页面里跳「提交记录」「参与者」写死的是老树的
//    名字（`TasksSubmissions` 那一套），路由名全应用唯一，所以在新外壳里点一下会
//    连人带页掉回老页面，外壳白套。`shellRouteNames.ts` 那一组就是为这件事留的：
//    这里 provide 新树的名字，老树那边不 provide、拿默认值，两边都不用改页面。
// 2. **页头**。老树里页头（题目那排 Tab + 编辑/删除）是 `PageHeader.vue` 作为
//    `header` 具名视图渲染的，新外壳没有那个插槽。这里显式渲染一次 —— Tab 与操作区
//    它自己从 `navigation` store 取，`views/tasks/Detail.vue` 挂载时会 `setTabs` /
//    `setActions`，两边不用互相知道。
//
// 页头里那颗「回到题目板」是这一层加的：面包屑在老树里是 `PageHeader` 按路由 meta
// 拼的，新树那几条没有 meta.title，拼不出来，所以给一颗说得出去处的按钮。
import { computed, provide } from 'vue'
import { useRoute } from 'vue-router'

import { BOARD_TASK_ROUTE_NAMES } from '../routeNames'
import { space } from '../store'

import PageHeader from '@/components/common/PageHeader.vue'
import { TASK_ROUTE_NAMES } from '@/lib/shellRouteNames'
import TaskDetailView from '@/views/tasks/Detail.vue'

provide(TASK_ROUTE_NAMES, BOARD_TASK_ROUTE_NAMES)

const route = useRoute()
const spaceId = computed(() => route.params.spaceId as string)
const homeTo = computed(() => ({ name: 'SpaceBoardHome', params: { spaceId: spaceId.value } }))
</script>

<template>
  <PageHeader>
    <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" :to="homeTo">
      {{ space?.name ?? '题目板' }}
    </v-btn>
  </PageHeader>

  <TaskDetailView />
</template>
