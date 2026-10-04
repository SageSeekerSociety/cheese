<script setup lang="ts">
// 手写一份技能或修改一份：名称、调用名、用途、正文、配套文件。和导入的是同一种格式。
// 新建时正文预填三个小标题；调用名跟着名称自动给，人改过一次就不再跟。桌面上是对话框，
// 手机上是整页（保存在页头右边，不会被键盘盖住）。
import type { ProjectSkill, ProjectSkillContent } from '@/lib/projectSkill'

import { reactive, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { suggestSkillName } from '@/lib/projectSkill'

const props = defineProps<{
  /** `'new'` 是新建；一份技能是修改它；null 是关着。 */
  editing: ProjectSkill | 'new' | null
  /** 修改时那一份配套文件的内容。 */
  contents: Record<string, string> | null
  /** 项目里已经用掉的调用名，自动给的不和它们撞。 */
  taken: string[]
  saving: boolean
  error: string
}>()

const emit = defineEmits<{
  close: []
  save: [value: ProjectSkillContent & { name: string }]
}>()

const form = reactive({
  name: '',
  title: '',
  description: '',
  body: '',
  files: [] as { path: string; content: string }[],
})
// 人动过调用名就不再跟着名称改。
const nameTouched = ref(false)
// 关上的那一下 editing 已经是 null，标题还要照着刚才那一条画完收起的动画。
const heading = ref('')

watch(
  () => props.editing,
  (value) => {
    if (!value) return
    nameTouched.value = value !== 'new'
    if (value === 'new') {
      heading.value = t('work.skills.newTitle')
      Object.assign(form, {
        name: suggestSkillName('', props.taken),
        title: '',
        description: '',
        body: t('work.skills.form.bodyTemplate'),
        files: [],
      })
    } else {
      heading.value = t('work.skills.editTitle', { title: value.title })
      Object.assign(form, {
        name: value.name,
        title: value.title,
        description: value.description,
        body: value.body,
        files: Object.entries(props.contents ?? {}).map(([path, content]) => ({ path, content })),
      })
    }
  },
  { immediate: true }
)

watch(
  () => form.title,
  (title) => {
    if (props.editing === 'new' && !nameTouched.value) form.name = suggestSkillName(title, props.taken)
  }
)

function save() {
  emit('save', {
    name: form.name.trim(),
    title: form.title,
    description: form.description,
    body: form.body,
    files: Object.fromEntries(form.files.filter((f) => f.path.trim()).map((f) => [f.path.trim(), f.content])),
  })
}
</script>

<template>
  <AdaptiveDialog
    :model-value="!!editing"
    :title="heading"
    :primary-label="t('work.skills.save')"
    :primary-loading="saving"
    size="lg"
    @update:model-value="emit('close')"
    @primary="save"
  >
    <template v-if="editing">
      <v-text-field
        v-model="form.title"
        autocomplete="off"
        :label="t('work.skills.form.title')"
        :placeholder="t('work.skills.form.titlePlaceholder')"
      />
      <v-text-field
        v-if="editing === 'new'"
        v-model="form.name"
        autocomplete="off"
        class="skill-form__name"
        :label="t('work.skills.form.name')"
        @update:model-value="nameTouched = true"
      />
      <v-textarea
        v-model="form.description"
        autocomplete="off"
        :label="t('work.skills.form.description')"
        rows="2"
        auto-grow
      />
      <v-textarea
        v-model="form.body"
        autocomplete="off"
        class="skill-form__body"
        :label="t('work.skills.fields.body')"
        rows="10"
        auto-grow
      />
      <div class="t-meta c-muted mb-2">{{ t('work.skills.fields.files') }}</div>
      <div v-for="(f, i) in form.files" :key="i" class="skill-file">
        <v-text-field
          v-model="f.path"
          autocomplete="off"
          :label="t('work.skills.form.path')"
          placeholder="scripts/check.py"
        />
        <v-textarea
          v-model="f.content"
          autocomplete="off"
          :label="t('work.skills.form.content')"
          rows="3"
          auto-grow
          class="skill-file__body"
        />
        <BaseButton size="sm" @click="form.files.splice(i, 1)">{{ t('work.skills.form.removeFile') }}</BaseButton>
      </div>
      <BaseButton kind="secondary" size="sm" @click="form.files.push({ path: '', content: '' })">
        {{ t('work.skills.form.addFile') }}
      </BaseButton>
      <p v-if="error" role="alert" class="t-body c-danger mt-2">{{ error }}</p>
    </template>
  </AdaptiveDialog>
</template>

<style scoped>
.skill-form__name :deep(input),
.skill-form__body :deep(textarea),
.skill-file__body :deep(textarea) {
  font-family: var(--font-mono, monospace);
}

.skill-file {
  border-left: 2px solid var(--line);
  padding-left: 8px;
  margin-bottom: 8px;
}
</style>
