<script setup lang="ts">
// 人自己提交审阅那个小面板（`HandInDialog`）的接线：读页面取的那一份采纳卡
// （`provideAcceptCard`）里的提交状态，页头点「提交审阅」「再提交一版」打开的就是它。
// 站在 `components/accept/` 之外，理由同 `DeliveryReviewHead`：面板组件只凭 props 画。
import { computed } from 'vue'

import { injectAcceptCard } from '../../composables/useAcceptCard'
import HandInDialog from '../accept/HandInDialog.vue'

import { t } from '@/i18n'
import { memberName } from '@/lib/agentNames'

const accept = injectAcceptCard()
const handIn = accept?.handIn ?? null

// 能审阅的人：自己排在最前，写「（你）」。
const reviewers = computed(() => {
  if (!accept) return []
  const me = accept.author.value
  const people = accept.reviewerChoices.value.map((m) => ({
    handle: m.user_handle,
    name: memberName(m) || m.user_handle,
  }))
  const mine = people.find((p) => p.handle === me)
  const others = people.filter((p) => p.handle !== me)
  return [
    ...(mine ? [{ handle: mine.handle, name: t('work.room.accept.handIn.you', { name: mine.name }) }] : []),
    ...others,
  ]
})
</script>

<template>
  <HandInDialog
    v-if="handIn"
    :model-value="handIn.open.value"
    :again="handIn.again.value"
    :adopts="handIn.adopts.value"
    :files="handIn.files.value"
    :subject="handIn.subject.value"
    :body="handIn.body.value"
    :reviewer="handIn.reviewer.value"
    :reviewers="reviewers"
    :drafting="handIn.drafting.value"
    :submitting="handIn.submitting.value"
    :ready="handIn.ready.value"
    @update:model-value="(open: boolean) => !open && handIn?.closePanel()"
    @update:subject="handIn.touchSubject"
    @update:body="handIn.touchBody"
    @update:reviewer="(value: string | null) => handIn && (handIn.reviewer.value = value)"
    @submit="handIn.submit()"
  />
</template>
