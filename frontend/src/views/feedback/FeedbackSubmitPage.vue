<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import FeedbackSubmitPageView from './FeedbackSubmitPageView.vue'

import { useSubmitFeedbackForm } from '@/components/feedback/useSubmitFeedbackForm'
import { stepBack } from '@/lib/backOut'
import { useFeedbackStore } from '@/stores/feedback'

// 提交反馈**页面**（`/feedback/new`）。反馈中心和「我的反馈」两个入口都到这一页。
//
// 为什么是页面而不是那个右侧抽屉：
//
//   * 一行字要填半天的时候，页面比浮层少一层「我在哪、关掉会不会丢」的疑问。
//   * **刷新之后人还停在表单上**。路由本身就是状态，而抽屉/弹窗刷新后会关掉 —— 草稿
//     虽然一直在盘上（见 lib/feedbackDraft.ts），可界面没了，人要自己去把表单找回来。
//   * 文档里的既有结论也指向它：反馈中心是一个完整页面，塞进浮层里那套（可分享的
//     链接、Tab、详情）一件都做不了。
//
// 会话里那张 agent 提案卡**不走这一页**，走对话框（`SubmitFeedbackDialog` →
// `SubmitFeedbackForm`）：从对话里跳走会把那张卡留在身后，而它提交完要就地翻成一张凭据。
//
// 拆成容器 + 视图是为了场景棘轮：视图（`FeedbackSubmitPageView.vue`）要能被单独渲染，
// 所以它不能读 store、不能读路由；这一半拿 store 和路由，把草稿和几个动作交给
// `useSubmitFeedbackForm` —— 表单的接线和对话框那一半**是同一份**，两边不会各长一套。
//
// `loadMeta` 在这里调，不在表单里：只有从页面进来的人需要它把类型词表拉下来（对话框那
// 一路是提案卡已经把草稿填好才打开的）。表单自己也调一次是以前的事，现在两边都不重复调。
defineOptions({ name: 'FeedbackSubmitPage' })

const store = useFeedbackStore()
const router = useRouter()

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

onMounted(() => {
  void store.loadMeta()
})

async function onSubmit() {
  const id = await submit()
  if (!id) return
  // **replace 而不是 push**：提交完还按回退键，人不该回到一张已经清空的表单上 ——
  // 那份内容已经变成一条反馈了，回到空表单只会让人以为刚才那次提交没成功。
  void router.replace({ name: 'FeedbackDetail', params: { id } })
}

/** 取消 / 返回：回到来处，没有来处（直接输地址进来的）就回反馈中心。
 *
 *  不能只写 `router.back()`：深链打开时它会把整个应用退出，而人以为自己按的是
 *  「不填了、回去」。草稿在这里**留着**（`closeSubmit` 会收尾落盘）——「取消」是
 *  不提交了，不是把写的东西删掉；要删有表单上那个「丢弃草稿」。 */
function leave() {
  store.closeSubmit()
  stepBack(router, { name: 'FeedbackCenter' })
}
</script>

<template>
  <FeedbackSubmitPageView
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
    @cancel="leave"
  />
</template>
