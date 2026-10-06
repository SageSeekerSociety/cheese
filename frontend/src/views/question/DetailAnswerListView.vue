<template>
  <!-- This screen does one thing: draw the answers. So the drawing half is handed straight
       to `components/questions/AnswerListView`, props and events forwarded as-is.
       `$props` carries the declared props and `$attrs` the events (this component declares
       no emits of its own), so one `v-bind` passes both on. -->
  <AnswerListView v-bind="{ ...$props, ...$attrs }" />
</template>

<script setup lang="ts">
// 问题详情下「全部回答」**画的那一半**：屏幕上就是那一串回答。
//
// 取数（`composables/useAnswerList`）、写操作（`composables/useAnswerActions`）、
// 题主判断与读失败态都在容器 `DetailAnswerList.vue` 里算好，从 props 递进来；
// 事件原样往上传。这里只是把 `AnswerListView` 就位的那一层。
//
// props 的类型就是 `AnswerListView` 自己导出的那一份（`import type` 不算依赖），不用
// 抄第二遍；声明出来是为了让容器那十几条绑定仍然被类型检查。
import type { AnswerListViewProps } from '@/components/questions/AnswerListView.vue'

import AnswerListView from '@/components/questions/AnswerListView.vue'

defineOptions({ inheritAttrs: false })

defineProps<AnswerListViewProps>()
</script>
