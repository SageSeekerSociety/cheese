<template>
  <v-dialog :model-value="category !== null" max-width="560" @update:model-value="(open) => !open && emit('close')">
    <v-card v-if="category">
      <v-card-title>{{ t('spaces.detail.manageCategories.teaching.title', { name: category.name }) }}</v-card-title>
      <v-card-text>
        <p class="t-body c-muted mb-4">{{ t('spaces.detail.manageCategories.teaching.intro') }}</p>
        <v-textarea
          v-model="systemPrompt"
          rows="4"
          auto-grow
          autocomplete="off"
          :label="t('spaces.detail.manageCategories.teaching.systemPrompt')"
          :hint="t('spaces.detail.manageCategories.teaching.systemPromptHint')"
          persistent-hint
          class="mb-4"
        />
        <v-text-field
          v-model="week"
          type="number"
          min="0"
          autocomplete="off"
          :label="t('spaces.detail.manageCategories.teaching.currentWeek')"
          :hint="t('spaces.detail.manageCategories.teaching.currentWeekHint')"
          persistent-hint
          class="mb-4"
        />
        <v-text-field
          v-model="topics"
          autocomplete="off"
          :label="t('spaces.detail.manageCategories.teaching.allowedTopics')"
          :hint="t('spaces.detail.manageCategories.teaching.listHint')"
          persistent-hint
          class="mb-4"
        />
        <v-text-field
          v-model="avoid"
          autocomplete="off"
          :label="t('spaces.detail.manageCategories.teaching.avoidInCode')"
          :hint="t('spaces.detail.manageCategories.teaching.avoidInCodeHint')"
          persistent-hint
          class="mb-4"
        />
        <v-text-field
          v-model="materialIds"
          autocomplete="off"
          :label="t('spaces.detail.manageCategories.teaching.materialIds')"
          :hint="t('spaces.detail.manageCategories.teaching.listHint')"
          persistent-hint
          class="mb-4"
        />
        <v-text-field
          v-model="knowledgeIds"
          autocomplete="off"
          :label="t('spaces.detail.manageCategories.teaching.knowledgeIds')"
          :hint="t('spaces.detail.manageCategories.teaching.listHint')"
          persistent-hint
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="emit('close')">{{ t('spaces.detail.manageCategories.cancel') }}</v-btn>
        <v-btn color="primary" variant="flat" :loading="saving" @click="save">
          {{ t('spaces.detail.manageCategories.confirm') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// 分类上「给芝士的指导」：从这个分类的题建出来的项目，芝士开新对话时读它
// （服务端见 `backend/app/domain/task/teaching.py`；单道题可以另设一份盖过它）。
import type { SpaceCategory } from '@/types'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useSpaceStore } from '@/stores/space'

const props = defineProps<{ category: SpaceCategory | null }>()
const emit = defineEmits<{ close: [] }>()

const { t } = useI18n()
const spaceStore = useSpaceStore()

const systemPrompt = ref('')
const week = ref('')
const topics = ref('')
const avoid = ref('')
const materialIds = ref('')
const knowledgeIds = ref('')
const saving = ref(false)

watch(
  () => props.category,
  (category) => {
    const config = category?.teaching ?? {}
    systemPrompt.value = config.systemPrompt ?? ''
    week.value = config.currentWeek == null ? '' : String(config.currentWeek)
    topics.value = (config.allowedTopics ?? []).join(', ')
    avoid.value = (config.avoidInCode ?? []).join(', ')
    materialIds.value = (config.materialIds ?? []).join(', ')
    knowledgeIds.value = (config.knowledgeIds ?? []).join(', ')
  },
  { immediate: true }
)

/** 逗号分隔（中英文逗号都认）→ 去掉空项。 */
function splitList(value: string): string[] {
  return value
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

function splitIds(value: string): number[] {
  return splitList(value)
    .map(Number)
    .filter((id) => Number.isInteger(id) && id > 0)
}

async function save() {
  if (!props.category || saving.value) return
  saving.value = true
  try {
    const w = week.value.trim()
    // 整份替换：省略 teaching 才是「不动它」。
    await spaceStore.updateCategory(props.category.id, {
      teaching: {
        systemPrompt: systemPrompt.value.trim() || null,
        currentWeek: w === '' ? null : Number(w),
        allowedTopics: splitList(topics.value),
        avoidInCode: splitList(avoid.value),
        materialIds: splitIds(materialIds.value),
        knowledgeIds: splitIds(knowledgeIds.value),
      },
    })
    emit('close')
  } catch {
    // 失败的提示由 store 给；弹窗留着，填的内容不丢。
  } finally {
    saving.value = false
  }
}
</script>
