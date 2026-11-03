<template>
  <DetailView
    v-model:bounty-dialog="bountyDialog"
    v-model:add-bounty-input="addBountyInput"
    v-model:invite-dialog="inviteDialog"
    :load-failed="loadFailed"
    :load-error="loadError"
    :question-data="questionData"
    :question-id="questionId"
    :content-html="contentHtml"
    :created-at="createdAt"
    :updated-at="updatedAt"
    :show-updated-at="showUpdatedAt"
    :author-avatar-url="authorAvatarUrl"
    :bounty-loading="bountyLoading"
    :invite-people="invitePeople"
    :invited-ids="invitedIds"
    @retry-load="retryLoad"
    @toggle-follow-question="toggleFollowQuestion"
    @attitude-question="attitudeQuestion"
    @add-bounty="addBounty"
    @open-my-answer="openMyAnswer"
    @create="onCreate"
    @submit="submit"
    @invite="invite"
  >
    <router-view />
  </DetailView>
</template>

<script setup lang="ts">
// 题目详情那一页的容器：从地址上认这是哪道题，读它、盯住地址换了再读，关注/投票/加悬赏/
// 发回答几个动作也在这里发请求；画面全在 `DetailView.vue`，中间那一格由路由认出来的子页
// 从插槽递进去。
import type EditorJS from '@editorjs/editorjs'
import type { Question } from '@/types'

import { computed, onMounted, provide, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'

import { getAvatarUrl } from '@/utils/materials'
import { parse } from '@/utils/parser'

import { ensureDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'
import { usePageTitle } from '@/composables/usePageTitle'
import { useQuestionInvitations } from '@/composables/useQuestionInvitations'

import { AnswersApi } from '@/api/answers'
import { QuestionApi } from '@/api/questions'
import { NewAttitudeType } from '@/constants'
import { questionDataInjectionKey } from '@/keys'
import { toast } from '@/lib/toast'
import DetailView from '@/views/question/DetailView.vue'

const { t } = useI18n()
let editor: EditorJS
const onCreate = (editorInstance: EditorJS) => {
  editor = editorInstance
}

const route = useRoute()
const router = useRouter()
const { setDynamicTitle } = usePageTitle()

const addBountyInput = ref<number>(1)
const bountyDialog = ref(false)
const bountyLoading = ref(false)
const inviteDialog = ref(false)

const questionId = computed(() => parseInt(route.params.questionId as string))

const questionData = ref<Question | null>(null)
// 读失败与「还没加载出来」是两件事：失败替换掉这张卡片，不再一直停在骨架上。
const loadFailed = ref(false)
const loadError = ref<string | null>(null)

provide(questionDataInjectionKey, questionData)

// 邀请弹窗里那份名单：弹窗开着才读（名单在弹窗里懒挂载，不开没人看得见，那两趟请求
// 白发）。读完的名单和「已经邀请过谁」交给画面，点人发邀请也在这里发。
const { people: invitePeople, invited, invite } = useQuestionInvitations(questionId, { active: inviteDialog })
const invitedIds = computed(() => invited.value.map((user) => user.id))

// 提问者的头像：注册时 profile 一律被填上默认头像 id，不能直接 getAvatarUrl——那样
// 没挑过头像的人会长着和所有人同一张脸。先问一次全局默认头像 id（幂等，全进程共用一
// 份），再据此判断这条 id 是不是本人真挑的；不是就把空串交给 UserAvatar，由它按 handle
// 画彩色首字母。默认 id 到货时这个 ref 一变，模板跟着重算。
ensureDefaultAvatarId()

const authorAvatarUrl = computed(() => {
  if (questionData.value && isChosenAvatar(questionData.value.author.avatarId)) {
    return getAvatarUrl(questionData.value.author.avatarId)
  }
  return ''
})

const createdAt = computed(() => {
  if (questionData.value) {
    return dayjs(questionData.value.created_at).fromNow()
  }
  return ''
})
const showUpdatedAt = computed(() => {
  if (questionData.value) {
    return dayjs(questionData.value.created_at).isBefore(questionData.value.updated_at)
  }
  return false
})
const updatedAt = computed(() => {
  if (questionData.value) {
    return dayjs(questionData.value.updated_at).fromNow()
  }
  return ''
})

const contentHtml = computed(() => {
  if (questionData.value) {
    return parse(JSON.parse(questionData.value.content))
  }
  return ''
})

const addBounty = async () => {
  bountyLoading.value = true
  try {
    await QuestionApi.addBounty(questionData.value!.id, addBountyInput.value)
    toast.success(t('questions.detail.addBountySuccess'))
    bountyDialog.value = false
  } catch (error) {
    toast.error(`${error}`)
  } finally {
    bountyLoading.value = false
  }
}

const load = async (id: number) => {
  loadFailed.value = false
  loadError.value = null
  try {
    const { question } = await QuestionApi.detail(id)
    questionData.value = question
  } catch (error) {
    console.error('Failed to load the question', error)
    loadFailed.value = true
    loadError.value = error instanceof Error && error.message ? error.message : null
  }
}

const retryLoad = () => load(questionId.value)

const submit = async () => {
  try {
    const outputData = await editor.save()
    if (outputData.blocks.length === 0) {
      toast.error(t('questions.detail.postAnswerEmpty'))
      return
    }
    const content = JSON.stringify(outputData)
    const data = await AnswersApi.answerQuestion(questionData.value!.id, content)
    toast.success(t('questions.detail.postAnswerSuccess'))
    editor.clear()
    router.push({
      name: 'QuestionAnswer',
      params: { questionId: questionData.value!.id.toString(), answerId: data.id.toString() },
    })
  } catch (error) {
    toast.error(`${error}`)
  }
}

const attitudeQuestion = async (attitudeType: NewAttitudeType) => {
  const { attitudes } = await QuestionApi.attitudeQuestion(questionData.value!.id, attitudeType)
  questionData.value!.attitudes = attitudes
}

const toggleFollowQuestion = async () => {
  if (questionData.value!.is_follow) {
    const { follow_count: followCount } = await QuestionApi.unfollowQuestion(questionData.value!.id)
    questionData.value!.follow_count = followCount
    questionData.value!.is_follow = false
  } else {
    const { follow_count: followCount } = await QuestionApi.followQuestion(questionData.value!.id)
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
