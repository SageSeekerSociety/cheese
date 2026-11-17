<template>
  <MyLearningInsightView
    :insight="insight"
    :loading="state === 'loading'"
    :failed="state === 'error'"
    :disputing="disputing"
    @dispute="onDispute"
    @retry="load"
  />
</template>

<script setup lang="ts">
// 「我的学习洞察」的容器：读我自己在这块板的学情分析结果，接住「标记不准」。
// 画面在 `MyLearningInsightView.vue`（场景规则见 docs/manual/dev/scenes.md）。
//
// 只读本人那一份：接口不收「看谁」，这一页也不传。标记失败就说服务端那句话
// （「这条判断不存在，可能已经更新了」这类），比一句「操作失败」有用。
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { useMyLearningInsight } from '@/composables/useSpaceLearningAnalysis'

import MyLearningInsightView from './MyLearningInsightView.vue'

import { axiosErrorMessage } from '@/api/axiosHttp'
import { toast } from '@/lib/toast'

const { t } = useI18n()
const route = useRoute()

const { insight, state, disputing, load, dispute } = useMyLearningInsight(() => Number(route.params.spaceId))

async function onDispute(judgmentId: number) {
  try {
    await dispute(judgmentId)
  } catch (failure) {
    toast.error(axiosErrorMessage(failure, t('spaces.learningAnalysis.insight.disputeFailed')))
  }
}
</script>
