<template>
  <!-- This screen does one thing: draw the answers. So the drawing half is handed straight
       to `components/questions/AnswerListView`, props and events forwarded as-is
       (`inheritAttrs: false` leaves them in `$attrs`, one `v-bind` passes them on). -->
  <AnswerListView v-bind="$attrs" />
</template>

<script setup lang="ts">
// 问题详情下「全部回答」**画的那一半**：屏幕上就是那一串回答。
//
// 取数（`composables/useAnswerList`）、写操作（`composables/useAnswerActions`）、
// 题主判断与读失败态都在容器 `DetailAnswerList.vue` 里算好，从 props 递进来；
// 事件原样往上传。这里只是把 `AnswerListView` 就位的那一层。
import AnswerListView from '@/components/questions/AnswerListView.vue'

defineOptions({ inheritAttrs: false })
</script>
