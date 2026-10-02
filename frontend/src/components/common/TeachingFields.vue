<script setup lang="ts">
// 「给 AI 队友的指导」那六格（#944）。空间设置的默认与发题页的覆盖共用这一份 ——
// 同一批字段、同一套「整份替换」的语义，两处各写一遍迟早走样。
//
// 默认只摆头两格：角色设定是这份指导唯一真要紧的东西（写不写它决定这份指导有没有
// 用），周次是它最常用的那一个占位；其余四格是细调，折进「高级选项」。判据是
// 「这一步不做会怎样」—— 不填话题范围、不填引用，指导照样成立。
import type { TeachingDraft } from '@/lib/teaching'
import type { SpaceTeaching } from '@/types'

import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { buildTeaching, draftFromConfig, emptyTeachingDraft } from '@/lib/teaching'

const props = defineProps<{ modelValue?: SpaceTeaching | null }>()
const emit = defineEmits<{ 'update:modelValue': [SpaceTeaching] }>()

const { t } = useI18n()

const draft = reactive<TeachingDraft>(emptyTeachingDraft())
/** 正把外面那一份抄进表单：这期间草稿的每一次改动都不是人敲的，别报回去。 */
let syncing = false

watch(
  () => props.modelValue,
  (config) => {
    // 人每敲一下，父组件手里那一份就是这里报上去的 `buildTeaching(draft)`；它转个
    // 圈又回到这一格。**跟表单现在这份一个意思就别抄回去** —— 抄一遍会把「刚敲下
    // 去的那个空格」这类还没成形的输入就地抹掉（`buildTeaching` 会 trim）。
    if (JSON.stringify(buildTeaching(draft)) === JSON.stringify(buildTeaching(draftFromConfig(config)))) return
    syncing = true
    Object.assign(draft, draftFromConfig(config))
    syncing = false
  },
  { immediate: true, deep: true }
)

// `flush: 'sync'`：抄进来的那几次赋值要在 `syncing` 还是 true 的时候就被这次
// 监听看见（默认的 pre/异步会让它们落在这行之后，白抄一趟还回弹一次）。
watch(
  draft,
  () => {
    if (!syncing) emit('update:modelValue', buildTeaching(draft))
  },
  { deep: true, flush: 'sync' }
)
</script>

<template>
  <div class="teaching-fields">
    <v-textarea
      v-model="draft.systemPrompt"
      rows="4"
      auto-grow
      autocomplete="off"
      variant="outlined"
      density="compact"
      data-testid="teaching-system-prompt"
      :label="t('spaces.teaching.fields.systemPrompt')"
      :hint="t('spaces.teaching.fields.systemPromptHint')"
      persistent-hint
    />
    <v-text-field
      v-model="draft.currentWeek"
      type="number"
      min="0"
      autocomplete="off"
      variant="outlined"
      density="compact"
      data-testid="teaching-current-week"
      :label="t('spaces.teaching.fields.currentWeek')"
      :hint="t('spaces.teaching.fields.currentWeekHint')"
      persistent-hint
    />

    <v-expansion-panels variant="accordion" flat class="teaching-fields__more">
      <v-expansion-panel :title="t('spaces.teaching.fields.advanced')" :elevation="0">
        <v-expansion-panel-text>
          <v-text-field
            v-model="draft.allowedTopics"
            autocomplete="off"
            variant="outlined"
            density="compact"
            data-testid="teaching-allowed-topics"
            :label="t('spaces.teaching.fields.allowedTopics')"
            :hint="t('spaces.teaching.fields.listHint')"
            persistent-hint
            class="mb-4"
          />
          <v-text-field
            v-model="draft.avoidInCode"
            autocomplete="off"
            variant="outlined"
            density="compact"
            data-testid="teaching-avoid-in-code"
            :label="t('spaces.teaching.fields.avoidInCode')"
            :hint="t('spaces.teaching.fields.avoidInCodeHint')"
            persistent-hint
            class="mb-4"
          />
          <v-text-field
            v-model="draft.materialIds"
            autocomplete="off"
            variant="outlined"
            density="compact"
            data-testid="teaching-material-ids"
            :label="t('spaces.teaching.fields.materialIds')"
            :hint="t('spaces.teaching.fields.listHint')"
            persistent-hint
            class="mb-4"
          />
          <v-text-field
            v-model="draft.knowledgeIds"
            autocomplete="off"
            variant="outlined"
            density="compact"
            data-testid="teaching-knowledge-ids"
            :label="t('spaces.teaching.fields.knowledgeIds')"
            :hint="t('spaces.teaching.fields.listHint')"
            persistent-hint
          />
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </div>
</template>

<style scoped>
.teaching-fields {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.teaching-fields__more {
  background: transparent;
}
</style>
