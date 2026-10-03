<script setup lang="ts">
// 「给 AI 队友的指导」那几格（#944）。空间设置的默认与发题页的覆盖共用这一份 ——
// 同一批字段、同一套「整份替换」的语义，两处各写一遍迟早走样。
//
// 默认只摆三格：「对 AI 的要求」是这份指导唯一真要紧的东西（写不写它决定这份指导有
// 没有用），周次是它最常用的那一个占位，参考资料是老师最常要给的那一样；其余三格是
// 细调，折进「高级选项」。判据是「这一步不做会怎样」—— 不填话题范围、不填知识条目，
// 指导照样成立。
//
// 「对 AI 的要求」留空就是一条要求都不加，所以这里不预填：框里的灰字是那份默认要求
// 的全文，旁边那颗按钮点一下才填进去（填进去之后照常改）。
import type { TeachingDraft } from '@/lib/teaching'
import type { SpaceMaterial, SpaceMaterialsState, SpaceTeaching } from '@/types'

import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import TeachingMaterialPicker from './TeachingMaterialPicker.vue'

import { buildTeaching, draftFromConfig, emptyTeachingDraft, splitIds } from '@/lib/teaching'

const props = defineProps<{
  modelValue?: SpaceTeaching | null
  /** 这块板资料库的清单。取数在容器那边（`GET /spaces/{id}/materials`），这里只摆。 */
  materials?: SpaceMaterial[]
  materialsState?: SpaceMaterialsState
  /** 这块板「资料库」页的地址。给出去，选择器里才有那条「上传到资料库」。 */
  libraryTo?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [SpaceTeaching] }>()

const { t } = useI18n()

const draft = reactive<TeachingDraft>(emptyTeachingDraft())

/** 参考资料那一格。草稿里存的是逗号分隔的原文（进接口还要走一遍 `splitIds`），
 *  选择器要的是编号数组，这里对一次。 */
const materialIds = computed<number[]>({
  get: () => splitIds(draft.materialIds),
  set: (ids) => {
    draft.materialIds = ids.join(', ')
  },
})

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

/** 把那份默认要求填进框里。留空时框里已经有它（灰字），这一颗是让老师接着改。 */
function useDefaultTemplate() {
  draft.systemPrompt = t('spaces.teaching.defaultTemplate')
}
</script>

<template>
  <div class="teaching-fields">
    <div class="teaching-fields__prompt">
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
        :placeholder="t('spaces.teaching.defaultTemplate')"
        persistent-hint
      />
      <v-btn
        size="small"
        variant="text"
        class="teaching-fields__default"
        data-testid="teaching-use-default-template"
        @click="useDefaultTemplate"
      >
        {{ t('spaces.teaching.useDefaultTemplate') }}
      </v-btn>
    </div>
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

    <TeachingMaterialPicker
      v-model="materialIds"
      :materials="props.materials ?? []"
      :state="props.materialsState ?? 'ready'"
      :library-to="props.libraryTo"
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

/* 那份默认要求的按钮贴在框的右上角：灰字提示在框里，一眼看到哪儿能一次填进去。 */
.teaching-fields__prompt {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.teaching-fields__prompt > :first-child {
  align-self: stretch;
}

.teaching-fields__default {
  margin-top: 2px;
  text-transform: none;
}
</style>
