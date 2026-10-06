<script setup lang="ts">
import type { SubmitFeedbackFormViewProps } from '@/components/feedback/SubmitFeedbackFormView.vue'

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
// 表单要的那几栏和它发的事件一共十几条，类型直接用人家的（`SubmitFeedbackFormView`
// 自己导出的那一份，`import type` 不算依赖），**不再抄一遍** —— 抄一遍只会多出一处会
// 漂开的地方。`shell` 抠掉了：那一栏是**这一页**定的（`page`），表单那半自己不知道
// 自己长在哪。声明出来还多一个好处：容器那十几条绑定重新被类型检查。
//
// 模板里 `v-bind="{ ...$props, ...$attrs }"`：props 走前者，事件（这一件只声明了
// `cancel`，其余都留在 `$attrs` 里）走后者，两边的键不会撞。
//
// `cancel` 是**这一页**的动作，不是表单的：返回箭头和表单里那个「不填了」出口是同一
// 件事。所以它显式接过来、显式往上发，容器只挂一个监听。
defineOptions({ name: 'FeedbackSubmitPageView', inheritAttrs: false })

defineProps<Omit<SubmitFeedbackFormViewProps, 'shell'>>()

const emit = defineEmits<{ (e: 'cancel'): void }>()
</script>

<template>
  <FeedbackPageShell :title="t('feedback.submit.title')" flush-bottom>
    <template #lead>
      <BaseButton icon="mdi-arrow-left" size="sm" :aria-label="t('feedback.submit.back')" @click="emit('cancel')" />
    </template>

    <SubmitFeedbackFormView v-bind="{ ...$props, ...$attrs }" shell="page" @cancel="emit('cancel')" />
  </FeedbackPageShell>
</template>
