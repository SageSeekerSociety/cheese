<script setup lang="ts">
// 「全部回答」这一页的容器：答案一页页取回来，采纳/投票/收藏在这里发请求，「这个人能
// 不能采纳」也在这里判。画面在 `DetailAnswerListView.vue`。
import { computed, inject, onMounted } from 'vue'

import { useAnswerActions } from '@/composables/useAnswerActions'
import { useAnswerPaging } from '@/composables/useAnswerPaging'
import { globalDefaultAvatarId } from '@/composables/useChosenAvatar'
import { useNavigation } from '@/composables/useNavigation'

import DetailAnswerListView from './DetailAnswerListView.vue'

import { questionDataInjectionKey } from '@/keys'

const nav = useNavigation()

const questionId = computed(() => Number(nav?.route?.params?.questionId))
/** 这道题本身由问题详情那一层 provide 进来。 */
const questionData = inject(questionDataInjectionKey)

const { data, refresh, refreshing } = useAnswerPaging(questionId)
const { canAccept, accept, upvote, downvote, cancelVote, favorite } = useAnswerActions(questionData)

onMounted(async () => {
  await refresh()
})
</script>

<template>
  <detail-answer-list-view
    :answers="data"
    :question="questionData"
    :refreshing="refreshing"
    :can-accept="canAccept"
    :default-avatar-id="globalDefaultAvatarId"
    @accept="accept"
    @upvote="upvote"
    @downvote="downvote"
    @cancel-vote="cancelVote"
    @favorite="favorite"
  />
</template>
