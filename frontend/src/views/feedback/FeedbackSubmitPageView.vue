<script setup lang="ts">
import BaseButton from '@/components/base/BaseButton.vue'
import FeedbackPageShell from '@/components/feedback/FeedbackPageShell.vue'
import SubmitFeedbackFormView from '@/components/feedback/SubmitFeedbackFormView.vue'
import { t } from '@/i18n'

// 提交反馈那一页（`/feedback/new`）的**画面那一半**：只吃 props、只往上发事件。
//
// 草稿的读、写、落盘、提交，以及提交完跳哪、取消回哪，都在
// `FeedbackSubmitPage.vue` 那一半。这一半画的是整页 —— 页头那副壳也跟着画面走（理由
// 和「我的反馈」那一页一样：壳管的是滚动和页边距，那是这一页长什么样的一部分）。
//
// 表单要的那几栏和它发的事件一共十几条，这里**不再抄一遍**，用 `v-bind="$attrs"`
// 原样透给 `SubmitFeedbackFormView` —— 抄一遍只会多出一处会漂开的地方，而真正定义
// 这些 props 和 emits 的地方是那个组件自己（先例：`views/feedback/AdminFeedbackPageView.vue`
// 对 `AdminQueuePageView`）。所以这里 `inheritAttrs: false`：属性不许落到壳的根节点上，
// 它们唯一的去处是那张表单。
//
// 只有 `cancel` 例外，它是**这一页**的动作，不是表单的：返回箭头和表单里那个「不填了」
// 出口是同一件事。声明成这里的 emit（而不是留在 `$attrs` 里）是为了让容器只挂一个
// 监听；表单自己那个 `cancel` 因此要显式接过来，它已经不在透传的那一份里了。
defineOptions({ name: 'FeedbackSubmitPageView', inheritAttrs: false })

const emit = defineEmits<{ (e: 'cancel'): void }>()
</script>

<template>
  <FeedbackPageShell :title="t('feedback.submit.title')" flush-bottom>
    <template #lead>
      <BaseButton icon="mdi-arrow-left" size="sm" :aria-label="t('feedback.submit.back')" @click="emit('cancel')" />
    </template>

    <SubmitFeedbackFormView v-bind="$attrs" shell="page" @cancel="emit('cancel')" />
  </FeedbackPageShell>
</template>
