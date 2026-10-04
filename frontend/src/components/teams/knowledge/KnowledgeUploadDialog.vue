<script setup lang="ts">
// 「添加资料」对话框：选类型、填这一档要的那几格、提交。
//
// 它自己拿着那份草稿（`KnowledgeDraft`）和 `VForm` 的校验 —— 填到一半的东西只有
// 它知道。校验过了就把整份草稿报上去（`submit`），之后的事全在外面：传材料、
// 建记录、说一句话、决定关不关。所以 `uploading` 是 props 进来的 —— 上传中它
// 只负责把按钮转起来。
//
// 打开一次就是新的一张表：`modelValue` 变 true 时把草稿和校验都清掉。原来的页
// 是在打开前清一次、成功后再清一次；对填表的人来说两者一样（他从没见过旧值）。
import type { KnowledgeDraft } from '@/lib/knowledgeDraft'
import type { KnowledgeType } from '@/types'

import { ref, watch } from 'vue'
import { VForm } from 'vuetify/lib/components/index.mjs'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import TipTapEditor from '@/components/common/Editor/TipTapEditor.vue'
import { t } from '@/i18n'
import { emptyKnowledgeDraft } from '@/lib/knowledgeDraft'
import {
  filePreviewUrl,
  fileTypeIcon,
  isImageFile,
  KNOWLEDGE_TYPE_OPTIONS,
  knowledgeTypeLabel,
  languageOptions,
  uploadTypeIcon,
} from '@/lib/knowledgeFormat'

defineOptions({ name: 'KnowledgeUploadDialog' })

const props = defineProps<{
  modelValue: boolean
  uploading: boolean
  availableTags: string[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [draft: KnowledgeDraft]
}>()

const form = ref<KnowledgeDraft>(emptyKnowledgeDraft())
const uploadForm = ref<InstanceType<typeof VForm>>()

function reset() {
  form.value = emptyKnowledgeDraft()
  uploadForm.value?.resetValidation()
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset()
  }
)

function pickType(type: KnowledgeType) {
  form.value.type = type
}

/** 校验过的草稿才出去；原件不递（交一份快照，免得外面改到里面这一份）。 */
async function submitUpload() {
  if (!uploadForm.value || !(await uploadForm.value.validate()).valid) {
    return
  }
  emit('submit', { ...form.value })
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="t('teams.knowledge.addResource')"
    :primary-label="t('teams.knowledge.uploadSubmit')"
    :primary-loading="uploading"
    :primary-disabled="uploading"
    :cancel-label="t('teams.knowledge.cancel')"
    @update:model-value="emit('update:modelValue', $event)"
    @primary="submitUpload"
  >
    <v-form ref="uploadForm" @submit.prevent="submitUpload">
      <!-- Resource name -->
      <v-text-field
        v-model="form.name"
        autocomplete="off"
        :label="t('teams.knowledge.name')"
        variant="outlined"
        hide-details="auto"
        class="mb-4"
        density="comfortable"
        :rules="[(v) => !!v || t('teams.knowledge.nameRequired')]"
      ></v-text-field>

      <div class="type-selector mb-5">
        <label class="text-body-2 text-medium-emphasis mb-3 d-block">{{
          t('teams.knowledge.resourceTypeLabel')
        }}</label>

        <div class="type-options">
          <div
            v-for="type in KNOWLEDGE_TYPE_OPTIONS"
            :key="type"
            class="type-option"
            :class="{ 'type-option-active': form.type === type }"
            @click="pickType(type)"
          >
            <div class="type-icon-wrapper">
              <v-icon :icon="uploadTypeIcon(type)" size="18"></v-icon>
            </div>
            <div class="type-label">{{ knowledgeTypeLabel(type) }}</div>
          </div>
        </div>
      </div>

      <div class="content-area">
        <!-- File upload area -->
        <div v-if="form.type === 'MATERIAL'" class="upload-content">
          <v-file-input
            v-model="form.file"
            :label="t('teams.knowledge.chooseFile')"
            variant="outlined"
            density="comfortable"
            accept="image/*,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,video/*,audio/*"
            :rules="[(v) => !!v || t('teams.knowledge.fileRequired')]"
            hide-details="auto"
            class="mb-4"
            show-size
            chips
            prepend-icon=""
          >
            <template #prepend>
              <v-icon color="primary" class="mr-2">mdi-file-upload-outline</v-icon>
            </template>
          </v-file-input>

          <div v-if="form.file" class="file-preview py-2">
            <v-img
              v-if="isImageFile(form.file)"
              :src="filePreviewUrl(form.file)"
              height="120"
              width="100%"
              class="rounded-lg mb-2"
              cover
            ></v-img>
            <!-- This used to carry a bare `grey-lighten-5` class: Vuetify 3 does not
                 generate such unprefixed palette classes (only v2 did), so it was
                 dead code with no background at all. Removed, so the next person does
                 not "helpfully fix" it by adding a `bg-` prefix — that would nail a
                 fixed-palette name into the template, and it would not follow theme
                 changes (exactly what the fixed-palette gate blocks). -->
            <div v-else class="d-flex align-center justify-center py-3 rounded-lg">
              <v-icon :icon="fileTypeIcon(form.file)" size="36" color="primary" class="mr-2"></v-icon>
              <span class="text-body-2">{{ form.file.name }}</span>
            </div>
          </div>
        </div>

        <!-- Rich-text editor area -->
        <div v-else-if="form.type === 'TEXT'" class="upload-content">
          <div class="mb-3">
            <TipTapEditor v-model="form.richTextContent" output="json" :min-height="180" />
          </div>
        </div>

        <!-- Link area -->
        <div v-else-if="form.type === 'LINK'" class="upload-content">
          <v-text-field
            v-model="form.url"
            autocomplete="off"
            :label="t('teams.knowledge.linkUrl')"
            variant="outlined"
            density="comfortable"
            hide-details="auto"
            class="mb-4"
            :rules="[
              (v) => !!v || t('teams.knowledge.linkUrlRequired'),
              (v) => /^https?:\/\//.test(v) || t('teams.knowledge.linkUrlInvalid'),
            ]"
            placeholder="https://"
            prepend-inner-icon="mdi-link"
          ></v-text-field>
          <v-text-field
            v-model="form.title"
            autocomplete="off"
            :label="t('teams.knowledge.linkTitle')"
            variant="outlined"
            density="comfortable"
            hide-details="auto"
            :placeholder="t('teams.knowledge.linkTitleHint')"
          ></v-text-field>
        </div>

        <!-- Code snippet area -->
        <div v-else-if="form.type === 'CODE'" class="upload-content">
          <v-select
            v-model="form.language"
            autocomplete="off"
            :label="t('teams.knowledge.language')"
            :items="languageOptions()"
            item-title="text"
            item-value="value"
            variant="outlined"
            density="comfortable"
            hide-details="auto"
            prepend-inner-icon="mdi-code-tags"
            class="mb-3"
          ></v-select>
          <v-textarea
            v-model="form.code"
            autocomplete="off"
            :label="t('teams.knowledge.codeContent')"
            variant="outlined"
            density="comfortable"
            :rules="[(v) => !!v || t('teams.knowledge.codeRequired')]"
            rows="6"
            hide-details="auto"
            :placeholder="t('teams.knowledge.codePlaceholder')"
            class="code-textarea"
            color="primary"
          ></v-textarea>
        </div>
      </div>

      <!-- Additional info area -->
      <div class="additional-info mt-4">
        <v-expansion-panels variant="accordion">
          <v-expansion-panel>
            <v-expansion-panel-title>
              <div class="d-flex align-center">
                <v-icon icon="mdi-information-outline" size="small" class="mr-2"></v-icon>
                {{ t('teams.knowledge.additionalInfo') }}
              </div>
            </v-expansion-panel-title>
            <v-expansion-panel-text>
              <v-textarea
                v-model="form.description"
                autocomplete="off"
                :label="t('teams.knowledge.descriptionLabel')"
                variant="outlined"
                density="comfortable"
                rows="2"
                hide-details="auto"
                class="mb-3"
                :placeholder="t('teams.knowledge.descriptionPlaceholder')"
              ></v-textarea>

              <v-combobox
                v-model="form.labels"
                autocomplete="off"
                :label="t('teams.knowledge.tags')"
                variant="outlined"
                density="comfortable"
                multiple
                chips
                closable-chips
                hide-details="auto"
                :items="availableTags"
                :placeholder="t('teams.knowledge.tagsPlaceholder')"
              ></v-combobox>
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
      </div>
    </v-form>
  </AdaptiveDialog>
</template>

<style scoped lang="scss">
.type-selector {
  margin-bottom: 24px;
}

.type-options {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.type-option {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 16px;
  border-radius: 12px;
  cursor: pointer;
  transition:
    background-color 0.2s cubic-bezier(0.4, 0, 0.2, 1),
    transform 0.2s cubic-bezier(0.4, 0, 0.2, 1),
    border-color 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  background-color: var(--fill);
  border: 1px solid transparent;

  &:hover {
    background-color: var(--fill-2);
    transform: translateY(-2px);
  }

  &.type-option-active {
    background-color: rgba(var(--v-theme-primary), 0.08);
    border-color: rgba(var(--v-theme-primary), 0.2);

    .type-icon-wrapper {
      background-color: rgb(var(--v-theme-primary));
      /* 琥珀底上的反白图标：深色主题的琥珀是 #FFA733，纯白只有 1.9:1 */
      color: var(--surface);
    }

    .type-label {
      color: rgb(var(--v-theme-primary));
      font-weight: 500;
    }
  }
}

.type-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background-color: var(--fill-2);
  transition:
    background-color 0.2s ease,
    color 0.2s ease;
}

.type-label {
  font-size: 0.875rem;
  transition:
    color 0.2s ease,
    font-weight 0.2s ease;
}

// 响应式调整
@media (max-width: 600px) {
  .type-options {
    grid-template-columns: repeat(2, 1fr);
  }
}

.upload-content {
  padding: 0;
  border-radius: 8px;
  min-height: 120px;
}

.file-preview {
  border-radius: 8px;
  overflow: hidden;
}

.code-textarea :deep(textarea) {
  font-family: 'Fira Code', monospace !important;
  font-size: 14px;
  line-height: 1.5;
}
</style>
