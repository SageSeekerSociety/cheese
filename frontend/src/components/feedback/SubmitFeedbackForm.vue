<script setup lang="ts">
import SubmitFeedbackFormView from './SubmitFeedbackFormView.vue'
import { useSubmitFeedbackForm } from './useSubmitFeedbackForm'

// 提交反馈那张表单的**对话框壳**：会话里那张 agent 提案卡走它。**只有它不跳页** ——
// 从对话里跳走会把「我刚看到的那张卡」留在身后，而卡片提交完要就地翻成一张凭证
// （`AgentFeedbackCard` 的 `submitted` 是组件内的 ref，跳页必丢）。页面那一路走
// `views/feedback/FeedbackSubmitPage.vue`，字段是同一份。
//
// 这个文件只做接线：草稿、词表、标签候选、提交都在 `useSubmitFeedbackForm` 里，画面在
// `SubmitFeedbackFormView.vue`（只吃 props、只发事件）。分开的理由是场景棘轮：页面壳
// 要能被单独渲染，而表单里那些「谁能碰 store」不该把整页拖进取数的那一档。
//
// 操作条在对话框壳里**不黏底**：对话框自己会滚（`SubmitFeedbackDialog` 的正文那一层）。
const emit = defineEmits<{ (e: 'submitted', id: string): void; (e: 'cancel'): void }>()

const {
  draft,
  kinds,
  restoredNotice,
  askRepro,
  askExpectation,
  canSubmit,
  tagSuggestions,
  submitting,
  error,
  patch,
  addTag,
  removeTag,
  discardDraft,
  submit,
} = useSubmitFeedbackForm()

async function onSubmit() {
  const id = await submit()
  // 失败时**什么都不关**：`error` 是服务端的原话，它就在表单上那块提示里，
  // 而人写的那几百字还在表单上 —— 按一下就能重试。
  if (id) emit('submitted', id)
}
</script>

<template>
  <SubmitFeedbackFormView
    shell="dialog"
    :draft="draft"
    :kinds="kinds"
    :restored-notice="restoredNotice"
    :ask-repro="askRepro"
    :ask-expectation="askExpectation"
    :tag-suggestions="tagSuggestions"
    :submitting="submitting"
    :error="error"
    :can-submit="canSubmit"
    @patch="patch"
    @add-tag="addTag"
    @remove-tag="removeTag"
    @discard-draft="discardDraft"
    @submit="onSubmit"
    @cancel="emit('cancel')"
  />
</template>
