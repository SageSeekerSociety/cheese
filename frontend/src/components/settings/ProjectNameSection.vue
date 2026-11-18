<script setup lang="ts">
// 项目名称：项目设置「名称和头像」那一页最上面。能改设置的人改，别人只看。改名不动
// 地址里的短名（那在「项目地址」里）。保存由页面去做（`composables/useProjectName.ts`）。
import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineOptions({ name: 'ProjectNameSection' })

/** 和建项目时一样的上限（后端 `ProjectCreate.name`）。 */
const MAX = 200

const props = defineProps<{
  name: string
  /** 不能改设置的人：只读。 */
  readonly: boolean
  saving: boolean
  /** 上一次保存为什么没成。 */
  error: string
}>()
const emit = defineEmits<{ save: [name: string] }>()

const draft = ref(props.name)
watch(
  () => props.name,
  (name) => (draft.value = name)
)

const typed = computed(() => draft.value.trim())
const savable = computed(() => !!typed.value && typed.value !== props.name && typed.value.length <= MAX)

function save() {
  if (!props.readonly && savable.value && !props.saving) emit('save', typed.value)
}
</script>

<template>
  <section class="page-section">
    <div class="page-section-body">
      <!-- Top-aligned: when an error appears under the field, the button stays beside it. -->
      <div class="d-flex align-start" style="gap: 8px">
        <v-text-field
          v-model="draft"
          :label="t('work.projectSettings.name.label')"
          :readonly="readonly"
          autocomplete="off"
          density="compact"
          variant="outlined"
          :maxlength="MAX"
          :error-messages="error ? [error] : []"
          hide-details="auto"
          style="flex: 1"
          data-user-content
          @keydown.enter="save"
        />
        <BaseButton
          v-if="!readonly"
          class="name-save"
          kind="primary"
          size="sm"
          :loading="saving"
          :disabled="!savable"
          @click="save"
        >
          {{ t('work.projectSettings.name.save') }}
        </BaseButton>
      </div>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
<style scoped>
/* 小号按钮比紧凑输入框矮 8px：上下各让一半，和输入框居中对齐。 */
.name-save {
  margin-top: 4px;
}
</style>
