<script setup lang="ts">
// 任务「改动」那一格的接线外壳。
//
// 这一格要的数据（树上标着什么、diff、打开的那一份读回来什么）都在
// `composables/usePanelChanges.ts` 里取，这一只负责接上去：把数传下去、把 ref 透出去。
// `components/panels/**` 下每个 SFC 都是场景棘轮里的「场景」，场景只吃 props 和事件，
// 取数一滴都不能漏进 `PanelChanges.vue` 或它下面。
//
// 外壳只能待在这儿：`components/panels/**` 底下每个 SFC 都是场景、包括外壳自己，所以
// 它得站在场景之外。面板自己也不能引这一只——场景的档位是顺着引用传下去的，
// 一个会取数的组件被面板引进来，面板就还是「自己取数的面板」。同一条理由见
// `components/work/PanelDocHost.vue`。
//
// 这一格顶部那块（这次交付的情况）也是在这里塞进去的（`AcceptReviewHead`）。
import type { ReviewBundle } from '../../types/reviewComment'

import { computed, ref } from 'vue'

import { injectAcceptCard } from '../../composables/useAcceptCard'
import { usePanelChanges } from '../../composables/usePanelChanges'
import PanelChanges from '../panels/PanelChanges.vue'

import AcceptReviewHead from './AcceptReviewHead.vue'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    projectId: string | null
    /** 这一格看的那件任务。 */
    taskId: string | null
    readOnly?: boolean
    // This tab is the one on screen. Loads happen on the rising edge, exactly
    // like opening the old drawer did.
    active?: boolean
    // Bumped by WorkPanel when a turn ends — the moment 芝士's commits and its
    // working tree actually changed. Silent re-fetch, never a spinner.
    refreshTick?: number
  }>(),
  { active: false, refreshTick: 0, readOnly: false }
)

// 整只 props 递进去，而不是抄一份值：取数那一层要跟着这几个 prop 变（换个房间、切
// 到这一格），抄出来的那一份是死的。
const changes = usePanelChanges(props)

// 批注跟着采纳卡那一份走：对话栏里的退回、横条上的「未发送」读的是同一份。
const accept = injectAcceptCard()
const review = computed<ReviewBundle | null>(() => {
  if (!accept) return null
  const c = accept.comments
  return {
    comments: c.shown.value,
    writable: c.writable.value && !props.readOnly,
    me: c.me,
    busy: c.busy.value,
    agentName: accept.agentName.value,
    add: c.add,
    edit: c.edit,
    remove: c.remove,
  }
})

const panel = ref<InstanceType<typeof PanelChanges> | null>(null)

// 地址里的 chip 指到某个文件时，工作面板会拿着文件路径来开这一格。
defineExpose({ openFile: (path: string) => panel.value?.openFile(path) })
</script>

<template>
  <PanelChanges ref="panel" :topic-id="props.topicId" :read-only="props.readOnly" :changes="changes" :review="review">
    <template #head><AcceptReviewHead /></template>
  </PanelChanges>
</template>
