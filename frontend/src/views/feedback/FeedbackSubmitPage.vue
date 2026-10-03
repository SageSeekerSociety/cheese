<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import FeedbackPageShell from '@/components/feedback/FeedbackPageShell.vue'
import SubmitFeedbackForm from '@/components/feedback/SubmitFeedbackForm.vue'
import { t } from '@/i18n'
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
// 会话里那张 agent 提案卡**不走这一页**，走对话框：从对话里跳走会把那张卡留在身后，
// 而它提交完要就地翻成一张凭证。字段是同一份（SubmitFeedbackForm），两个壳只是壳。
//
// 版面走 `FeedbackPageShell`（返回箭头在标题左边、表单在下面），**底边留白交给页内的
// 黏底操作条**（`flushBottom`）：`sticky` 量的是滚动容器内容盒的下沿，壳再加一层 48px
// 底内边距，操作条下面就会多出一条带子，表单从那里露出来。
defineOptions({ name: 'FeedbackSubmitPage' })

const store = useFeedbackStore()
const router = useRouter()

// 先把草稿准备好（手上这份有内容就接着写，空着才去盘上捞那一份），再谈画界面。
// 这一步在表单组件里也做了一次（它自己挂载时调），这里不重复调：两处都调的话，
// 第二次会把刚捞回来的那份再判成「手上这份没有内容」。
onMounted(() => {
  void store.loadMeta()
})

function onSubmitted(id: string) {
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
  <FeedbackPageShell :title="t('feedback.submit.title')" flush-bottom>
    <template #lead>
      <v-btn icon size="small" variant="text" color="secondary" :aria-label="t('feedback.submit.back')" @click="leave">
        <v-icon size="20">mdi-arrow-left</v-icon>
      </v-btn>
    </template>

    <SubmitFeedbackForm shell="page" @submitted="onSubmitted" @cancel="leave" />
  </FeedbackPageShell>
</template>
