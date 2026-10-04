<script setup lang="ts">
// 导入一份别处做的技能：先读（上传 SKILL.md 或技能文件夹的 .zip，或者填 GitHub 地址），
// 读出来摊开给人看正文和每个文件，确认才添加。外面来的技能会带进这个项目之后的每个会话，
// 里面的脚本会在工作电脑上运行，这一步是把关，所以只给项目管理员（入口在页面那一层判断）。
import type { ProjectSkillContent, SkillImportPreview } from '@/lib/projectSkill'

import { computed, reactive, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { MarkdownRenderer } from '@/components/chat/services/markdownRenderer'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  open: boolean
  /** 读出来的那一份；还没读是 null。 */
  preview: SkillImportPreview | null
  reading: boolean
  adding: boolean
  error: string
}>()

const emit = defineEmits<{
  close: []
  read: [source: { file: File } | { url: string }]
  back: []
  add: [value: ProjectSkillContent & { name: string }]
}>()

const markdown = new MarkdownRenderer()
const source = ref<'upload' | 'url'>('upload')
const file = ref<File | null>(null)
const url = ref('')
const form = reactive({ name: '', title: '', description: '' })

watch(
  () => props.open,
  (open) => {
    if (!open) return
    source.value = 'upload'
    file.value = null
    url.value = ''
  }
)
watch(
  () => props.preview,
  (p) => {
    if (p) Object.assign(form, { name: p.name, title: p.title, description: p.description })
  }
)

const canRead = computed(() => (source.value === 'upload' ? !!file.value : !!url.value.trim()))
const body = computed(() => (props.preview ? markdown.render(props.preview.body) : ''))
const files = computed(() =>
  Object.entries(props.preview?.files ?? {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([path, text]) => ({ path, kb: Math.max(1, Math.round(new Blob([text]).size / 1024)) }))
)

function pick(value: File | File[] | null) {
  file.value = Array.isArray(value) ? value[0] ?? null : value
}

function primary() {
  if (!props.preview) {
    if (source.value === 'upload' && file.value) emit('read', { file: file.value })
    else if (source.value === 'url') emit('read', { url: url.value.trim() })
    return
  }
  emit('add', {
    name: form.name.trim(),
    title: form.title,
    description: form.description,
    body: props.preview.body,
    files: props.preview.files,
  })
}
</script>

<template>
  <AdaptiveDialog
    :model-value="open"
    :title="
      preview ? t('work.skills.import.addTitle', { title: form.title || preview.title }) : t('work.skills.import.title')
    "
    :primary-label="preview ? t('work.skills.import.add') : t('work.skills.import.read')"
    :primary-loading="preview ? adding : reading"
    :primary-disabled="!preview && !canRead"
    size="lg"
    @update:model-value="emit('close')"
    @primary="primary"
  >
    <template v-if="!preview">
      <v-tabs v-model="source" density="compact" class="mb-4">
        <v-tab value="upload">{{ t('work.skills.import.upload') }}</v-tab>
        <v-tab value="url">{{ t('work.skills.import.url') }}</v-tab>
      </v-tabs>
      <v-file-input
        v-if="source === 'upload'"
        :model-value="file"
        accept=".md,.zip"
        prepend-icon=""
        prepend-inner-icon="mdi-paperclip"
        :label="t('work.skills.import.file')"
        data-import-file
        @update:model-value="pick"
      />
      <v-text-field
        v-else
        v-model="url"
        autocomplete="off"
        :label="t('work.skills.import.urlLabel')"
        placeholder="https://github.com/anthropics/skills/tree/main/skills/pdf"
        data-import-url
      />
    </template>

    <template v-else>
      <v-text-field v-model="form.title" autocomplete="off" :label="t('work.skills.form.title')" />
      <v-text-field v-model="form.name" autocomplete="off" class="import__name" :label="t('work.skills.form.name')" />
      <v-textarea
        v-model="form.description"
        autocomplete="off"
        :label="t('work.skills.form.description')"
        rows="2"
        auto-grow
      />
      <h3 class="t-eyebrow c-muted mb-2">{{ t('work.skills.fields.body') }}</h3>
      <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
      <div class="markdown-body import__body t-reading" data-user-content v-html="body" />
      <template v-if="files.length">
        <h3 class="t-eyebrow c-muted mt-4 mb-2">{{ t('work.skills.fields.files') }}</h3>
        <ul class="import__files">
          <li v-for="f in files" :key="f.path" class="t-body">
            <span class="import__path">{{ f.path }}</span>
            <span class="t-meta c-faint">{{ t('work.skills.detail.size', { kb: f.kb }) }}</span>
          </li>
        </ul>
      </template>
      <p v-if="preview.skipped.length" class="t-meta c-muted mt-2">
        {{ t('work.skills.import.skipped', { files: preview.skipped.join(t('work.skills.listSeparator')) }) }}
      </p>
      <p v-if="preview.scripts" class="import__warn t-body mt-3" data-import-scripts>
        {{ t('work.skills.import.scripts', { n: preview.scripts }) }}
      </p>
    </template>
    <p v-if="error" role="alert" class="t-body c-danger mt-2">{{ error }}</p>

    <template v-if="preview" #actions>
      <BaseButton kind="secondary" @click="emit('back')">{{ t('work.skills.import.back') }}</BaseButton>
    </template>
  </AdaptiveDialog>
</template>

<style scoped>
.import__name :deep(input),
.import__path {
  font-family: var(--font-mono, monospace);
}

.import__body {
  max-height: 320px;
  padding: 12px;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  overflow-wrap: anywhere;
}

.import__body :deep(h1),
.import__body :deep(h2),
.import__body :deep(h3) {
  margin: 12px 0 6px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.import__body :deep(p),
.import__body :deep(ul),
.import__body :deep(ol) {
  margin: 0 0 8px;
}

.import__body :deep(ul),
.import__body :deep(ol) {
  padding-left: 20px;
}

.import__files {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.import__files li {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.import__path {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.import__warn {
  margin-bottom: 0;
  padding: 8px 12px;
  background: var(--warn-wash);
  border-radius: var(--radius-md);
  color: var(--warn-ink);
}
</style>
