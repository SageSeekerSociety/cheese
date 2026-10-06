<template>
  <DetailView
    :question="questionData"
    :question-id="questionId"
    :failed="failed"
    :failure-reason="failureReason"
    :forbidden="forbidden"
    :bounty-loading="bountyLoading"
    :invite-users="inviteUsers"
    :invite-invited="inviteInvited"
    :resolve-user="resolveUser"
    :submit-answer="submitAnswer"
    :add-bounty="addBounty"
    :set-attitude="setAttitude"
    :toggle-follow="toggleFollow"
    :open-my-answer="openMyAnswer"
    :invite="invite"
    @retry="retryLoad"
    @navigate="navigate"
  />
</template>

<script setup lang="ts">
// 问题详情页（`/questions/:questionId`）：读路由、取这道题、页标题、`provide` 给子路由
// 的那道题、以及所有写操作（悬赏 / 发布回答 / 赞踩 / 关注 / 采纳链接 / 邀请）都在这儿；
// 画的那一半在 `DetailView.vue`，只吃 props、只发事件。
//
// `provide` 留在这一侧：回答列表 / 单条回答是本页的子路由（`DetailView` 里的
// `<router-view>`），它们照旧 `inject` 这道题。
import type { Question } from '@/types'

import { computed, onMounted, provide, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { useInvitationList } from '@/composables/useInvitationList'
import { usePageTitle } from '@/composables/usePageTitle'
import { useUserRefResolver } from '@/composables/useUserRefResolver'

import DetailView from './DetailView.vue'

import { NewAttitudeType } from '@/constants'
import { questionDataInjectionKey } from '@/keys'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { AnswersApi } from '@/network/api/answers'
import { QuestionApi } from '@/network/api/questions'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { setDynamicTitle } = usePageTitle()
const { resolve: resolveUser, navigate } = useUserRefResolver()

const bountyLoading = ref(false)

const questionId = computed(() => parseInt(route.params.questionId as string))

const questionData = ref<Question | null>(null)
// 读失败与「还没加载出来」是两件事：失败替换掉这张卡片，不再一直停在骨架上。
const loadError = ref<unknown>(null)
const failed = computed(() => loadError.value !== null)
const failureReason = computed(() => loadFailureReason(loadError.value))
// 是「不给你看」还是「这次没读到」，在这里判：画面只拿布尔值、那句话说，不认状态码。
const forbidden = computed(() => isForbidden(loadError.value))

provide(questionDataInjectionKey, questionData)

// 邀请弹窗里那份名单：可以邀请谁、已经请了谁。
const { users: inviteUsers, isInvited: inviteInvited, invite } = useInvitationList(() => questionId.value)

const addBounty = async (amount: number): Promise<boolean> => {
  bountyLoading.value = true
  try {
    await QuestionApi.addBounty(questionData.value!.id, amount)
    toast.success(t('questions.detail.addBountySuccess'))
    return true
  } catch (error) {
    toast.error(`${error}`)
    return false
  } finally {
    bountyLoading.value = false
  }
}

const load = async (id: number) => {
  loadError.value = null
  try {
    const {
      data: { question },
    } = await QuestionApi.detail(id)
    questionData.value = question
  } catch (error) {
    console.error('Failed to load the question', error)
    loadError.value = error
  }
}

const retryLoad = () => load(questionId.value)

const submitAnswer = async (content: string): Promise<boolean> => {
  try {
    const { data } = await AnswersApi.answerQuestion(questionData.value!.id, content)
    toast.success(t('questions.detail.postAnswerSuccess'))
    router.push({
      name: 'QuestionAnswer',
      params: { questionId: questionData.value!.id.toString(), answerId: data.id.toString() },
    })
    return true
  } catch (error) {
    toast.error(`${error}`)
    return false
  }
}

const setAttitude = async (attitudeType: NewAttitudeType) => {
  const {
    data: { attitudes },
  } = await QuestionApi.attitudeQuestion(questionData.value!.id, attitudeType)
  questionData.value!.attitudes = attitudes
}

const toggleFollow = async () => {
  if (questionData.value!.is_follow) {
    const {
      data: { follow_count: followCount },
    } = await QuestionApi.unfollowQuestion(questionData.value!.id)
    questionData.value!.follow_count = followCount
    questionData.value!.is_follow = false
  } else {
    const {
      data: { follow_count: followCount },
    } = await QuestionApi.followQuestion(questionData.value!.id)
    questionData.value!.follow_count = followCount
    questionData.value!.is_follow = true
  }
}

const openMyAnswer = () => {
  if (questionData.value?.my_answer_id) {
    router.push({
      name: 'QuestionAnswer',
      params: { questionId: questionData.value.id.toString(), answerId: questionData.value.my_answer_id.toString() },
    })
  }
}

onMounted(async () => {
  await load(questionId.value)
})

onBeforeRouteUpdate(async (to, from) => {
  if (to.params.questionId !== from.params.questionId) {
    await load(parseInt(to.params.questionId as string))
  }
})

watch(questionData, (newVal) => {
  if (newVal) {
    setDynamicTitle(newVal.title)
  }
})
</script>
