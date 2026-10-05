<template>
  <SettingsToolbar>
    <BaseButton kind="primary" prepend-icon="mdi-plus" @click="emit('create')">{{
      t('spaces.detail.manageTemplates.createTemplate')
    }}</BaseButton>
  </SettingsToolbar>
  <div class="settings-card">
    <v-list v-if="templates.length > 0" class="settings-list" bg-color="transparent" lines="three">
      <v-list-item v-for="(template, index) in templates" :key="index">
        <template #prepend>
          <v-icon size="18" class="c-faint">mdi-file-document-outline</v-icon>
        </template>
        <v-list-item-title class="tpl__name">{{ template.name }}</v-list-item-title>
        <div class="tpl__meta">
          <span v-if="template.title">{{ template.title }}</span>
          <span v-for="fact in facts(template)" :key="fact">{{ fact }}</span>
        </div>
        <p v-if="template.description" class="tpl__desc">{{ template.description }}</p>
        <template #append>
          <BaseButton
            kind="ghost"
            icon="mdi-pencil-outline"
            size="sm"
            :aria-label="t('spaces.detail.manageTemplates.edit')"
            @click="emit('edit', index)"
          />
          <BaseButton
            kind="ghost"
            icon="mdi-delete-outline"
            size="sm"
            :aria-label="t('spaces.detail.manageTemplates.delete')"
            @click="deleteTemplate(index)"
          />
        </template>
      </v-list-item>
    </v-list>

    <BaseEmptyState
      v-else
      size="inline"
      class="settings-empty"
      :title="t('spaces.detail.manageTemplates.noTemplates')"
    />
  </div>
</template>

<script setup lang="ts">
// 模板管理这一屏的画面：一列模板，每行的编辑/删除。去新建/编辑页、真删，都归容器
// `ManageTemplates.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceTaskTemplate } from '@/types'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'
import { useDialog } from '@/plugins/dialog'

defineProps<{ templates: SpaceTaskTemplate[] }>()

const emit = defineEmits<{ create: []; edit: [index: number]; delete: [index: number] }>()

const { t } = useI18n()
const { confirm } = useDialog()

// 模板里定了的那几项，列在名字下面一行。
const RANKS: Record<number, string> = {
  1: 'tasks.form.beginner',
  2: 'tasks.form.intermediate',
  3: 'tasks.form.advanced',
}

const facts = (template: SpaceTaskTemplate) => {
  const out: string[] = []
  if (template.submitterType)
    out.push(t(template.submitterType === 'USER' ? 'tasks.form.individual' : 'tasks.form.team'))
  if (template.rank && RANKS[template.rank]) out.push(t(RANKS[template.rank]))
  if (template.defaultDeadline)
    out.push(t('spaces.detail.manageTemplates.defaultDeadline', { n: template.defaultDeadline }))
  if (template.requireRealName != null)
    out.push(
      t(
        template.requireRealName ? 'spaces.detail.manageTemplates.realName' : 'spaces.detail.manageTemplates.noRealName'
      )
    )
  return out
}

const deleteTemplate = async (index: number) => {
  const result = await confirm(t('spaces.detail.manageTemplates.deleteConfirm'), {
    confirmLabel: t('spaces.detail.manageTemplates.delete'),
    danger: true,
  }).wait()
  if (!result) return

  emit('delete', index)
}
</script>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.tpl__name {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.tpl__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 0;
  margin-top: 2px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.tpl__meta > span + span::before {
  margin: 0 6px;
  color: var(--faint);
  content: '·';
}

.tpl__desc {
  margin: 4px 0 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
