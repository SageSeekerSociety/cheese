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
import { space, tasks } from '../store'

import PageHeader from '@/components/common/PageHeader.vue'
import { TASK_ROUTE_NAMES } from '@/lib/shellRouteNames'
import TaskDetailView from '@/views/tasks/Detail.vue'

provide(TASK_ROUTE_NAMES, BOARD_TASK_ROUTE_NAMES)

const route = useRoute()
const spaceId = computed(() => route.params.spaceId as string)
const homeTo = computed(() => ({ name: 'SpaceBoardHome', params: { spaceId: spaceId.value } }))

/** 出处那枚标记（「PDF · 第 3 页」）。
 *
 *  它读的是**外壳那份 store**，不是老详情页自己取的那道题：出处不是接口的一列，
 *  是简介开头的一段文本，只有 `store.ts` 的映射（`splitOrigin`）把它摘出来。老详情
 *  页不显示简介，所以正文里那串字在这儿本来也不会出现 —— 这枚标是唯一的呈现，
 *  不是补一个重复的。
 *
 *  代价：那道题不在外壳这一页列表里（`loadBoard` 只取 100 道）时认不出来，标不显示。
 *  拿它去另打一次详情接口不值 —— 为了一枚标把详情页多拖一轮。 */
const taskId = computed(() => route.params.taskId as string)
const origin = computed(() => tasks.value.find((t) => t.id === taskId.value)?.origin)
</script>

<template>
  <PageHeader>
    <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" :to="homeTo">
      {{ space?.name ?? '题目板' }}
    </v-btn>
    <v-chip v-if="origin" size="x-small" label variant="tonal" color="info" class="td__origin">
      {{ origin }}
    </v-chip>
  </PageHeader>

  <TaskDetailView />
</template>

<style scoped>
.td__origin {
  margin-left: 8px;
}
</style>
