<script setup lang="ts">
// 给资料库里的一份文件或一个文件夹起名字：「移动或重命名」和「新建文件夹」共用。
//
// 资料库的名字就是它的位置（`合同/2026/报价.xlsx`），所以「移动」和「改名」是同一件
// 事：改的都是这条路径。拆成「放在哪个文件夹」和「叫什么」两格填，是因为人想的是
// 这两件事，不是一条带斜杠的字符串。不给 `folders` 时只有名字那一格（新建文件夹，
// 放在哪由打开它的那一层决定）。
import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const open = defineModel<boolean>({ default: false })

const props = withDefaults(
  defineProps<{
    title: string
    primaryLabel: string
    /** 打开时填好的名字。 */
    name?: string
    /** 打开时填好的文件夹（`''` 是最上层）。 */
    folder?: string
    /** 可挑的文件夹；不给就不出「文件夹」那一格。 */
    folders?: string[]
    loading?: boolean
    error?: string
  }>(),
  { name: '', folder: '', folders: undefined, loading: false, error: '' }
)

const emit = defineEmits<{ submit: [path: string] }>()

const nameField = ref('')
const folderField = ref('')
watch(
  open,
  (isOpen) => {
    if (!isOpen) return
    nameField.value = props.name
    folderField.value = props.folder
  },
  { immediate: true }
)

const trimmed = (value: string | null) => (value ?? '').trim().replace(/^\/+|\/+$/g, '')
const valid = computed(() => !!trimmed(nameField.value) && !trimmed(nameField.value).includes('/'))

function submit() {
  if (!valid.value) return
  const folder = trimmed(folderField.value)
  emit('submit', folder ? `${folder}/${trimmed(nameField.value)}` : trimmed(nameField.value))
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="props.title"
    :primary-label="props.primaryLabel"
    :primary-loading="props.loading"
    :primary-disabled="!valid"
    :close-disabled="props.loading"
    size="sm"
    @primary="submit"
  >
    <form class="library-name" @submit.prevent="submit">
      <v-combobox
        v-if="props.folders"
        v-model="folderField"
        :items="props.folders"
        autocomplete="off"
        :label="t('work.library.folderField')"
        :placeholder="t('work.library.topLevel')"
        persistent-placeholder
        variant="outlined"
        density="comfortable"
        clearable
        hide-details
      />
      <v-text-field
        v-model="nameField"
        :label="t('work.library.nameField')"
        autocomplete="off"
        variant="outlined"
        density="comfortable"
        autofocus
        :error-messages="props.error || undefined"
        :hide-details="!props.error"
      />
    </form>
  </AdaptiveDialog>
</template>

<style scoped>
.library-name {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 8px;
}
</style>
