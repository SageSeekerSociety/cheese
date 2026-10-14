<script setup lang="ts">
import type { SourceRow } from '@/lib/adminModels'
import type { SourceDraft } from '@/lib/adminSources'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminSourceFields from '@/components/admin/models/AdminSourceFields.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { draftFromSource, draftValid, emptySourceDraft } from '@/lib/adminSources'

// 添加 / 编辑一条线路的框。和模型表单同一条分工：这一层收字、先按服务端的规则自查、
// 把草稿 emit 出去；打接口、失败时不关框，是页面的事（它同时看得到请求结果和框开没开）。
//
// 模型那一层的东西（上架、名字、档位、能力）不在这里：同一个模型每条线路必须一样，
// 服务端从已有那条线路抄过来，这个框连字段都不给，免得人以为能按线路改。
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    mode: 'add' | 'edit'
    /** 加到哪个模型下（框题里要说）。 */
    modelName: string
    /** `edit` 时那条线路。 */
    seed?: SourceRow | null
    /** `add` 时新线路的默认优先级：排在现有线路之后。 */
    nextOrder?: number
    saving?: boolean
    error?: string | null
  }>(),
  { seed: null, nextOrder: 1, saving: false, error: null }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'submit', draft: SourceDraft): void
}>()

const { t } = useI18n()

const draft = ref<SourceDraft>(emptySourceDraft())

// 每次打开都从头填：上一次留下的半截输入不该跟着下一次回来。
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    draft.value = props.mode === 'edit' && props.seed ? draftFromSource(props.seed) : emptySourceDraft(props.nextOrder)
  },
  { immediate: true }
)

const valid = computed(() => draftValid(draft.value))

function close() {
  emit('update:modelValue', false)
}

function submit() {
  if (!valid.value || props.saving) return
  emit('submit', draft.value)
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="
      mode === 'add'
        ? t('models.source.dialog.addTitle', { name: modelName })
        : t('models.source.dialog.editTitle', { name: modelName })
    "
    :primary-label="mode === 'add' ? t('models.dialog.create') : t('models.dialog.save')"
    :primary-loading="saving"
    :primary-disabled="!valid || saving"
    :persistent="saving"
    :close-disabled="saving"
    @update:model-value="!$event && close()"
    @primary="submit"
  >
    <AdminSourceFields v-model="draft" :editing="mode === 'edit'" />

    <v-alert v-if="error" type="error" density="compact" variant="tonal" class="asd__alert" role="alert">
      {{ error }}
    </v-alert>
  </AdaptiveDialog>
</template>

<style scoped>
.asd__alert {
  margin-top: 12px;
}
</style>
