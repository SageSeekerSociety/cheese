<template>
  <LearningAnalysisView
    :enabled="enabled ?? false"
    :knowledge-points="knowledgePoints"
    :can-manage="canManage"
    :loading="state === 'loading'"
    :failed="state === 'error'"
    :saving="saving"
    :saved="saved"
    :save-error="error"
    @save="save"
    @add-point="addPoint"
    @rename-point="renamePoint"
    @remove-point="removePoint"
    @retry="load"
  />
</template>

<script setup lang="ts">
// 空间设置里「学情分析」那一栏的容器：读这块板的开关 + 知识点清单，改开关，清单增删改。
// 画面在 `LearningAnalysisView.vue`（场景规则见 docs/manual/dev/scenes.md）。
//
// 三件事：
//
// - 开关走 `PATCH .../learning-analysis`（要 `edit_settings`）。这一块内容一直在屏幕上，
//   改完的结果**就地**说（§3.11 的 `useSaveState({feedback:'inline'})` + `SaveStatus`），
//   不用 toast —— 一条几秒就走掉的红条，人回头已经看不见了。
// - 清单的增删改各是一下就完的动作，一次一条；失败了就说服务端那句话（「已经有
//   「相关子查询」这条知识点了」这类），那条话本身就是可操作的信息，比「操作失败」有用。
// - 「读不出来」和「没开」分开（§3.10）：`state === 'error'` 时整块换成错误加重试，
//   不能让一次网络失败读成「这块板没开学情分析」。
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import LearningAnalysisView from './LearningAnalysisView.vue'

import { axiosErrorMessage } from '@/api/axiosHttp'
import { useSpaceLearningAnalysis } from '@/composables/useSpaceLearningAnalysis'
import { useSaveState } from '@/composables/useSaveState'
import { toast } from '@/lib/toast'

const { t } = useI18n()
const route = useRoute()

const spaceId = Number(route.params.spaceId)

const { enabled, knowledgePoints, canManage, state, load, setEnabled, addKnowledgePoint, renameKnowledgePoint, removeKnowledgePoint } =
  useSpaceLearningAnalysis(spaceId)

/** 开关那一次保存的回执（§3.11）：就地写在开关旁边。 */
const { saving, saved, error, run } = useSaveState({ feedback: 'inline' })

async function save(next: boolean) {
  await run(() => setEnabled(next))
}

/** 清单那几条各是一次性动作：当下不换页，失败就说服务端那句话。 */
async function guarded(action: () => Promise<void>, fallback: string) {
  try {
    await action()
  } catch (failure) {
    toast.error(axiosErrorMessage(failure, fallback))
  }
}

const addPoint = (name: string) =>
  guarded(() => addKnowledgePoint(name), t('spaces.learningAnalysis.knowledgePoint.save'))

const renamePoint = ({ id, name }: { id: number; name: string }) =>
  guarded(() => renameKnowledgePoint(id, name), t('spaces.learningAnalysis.knowledgePoint.save'))

const removePoint = (id: number) =>
  guarded(() => removeKnowledgePoint(id), t('spaces.learningAnalysis.knowledgePoint.remove'))
</script>
