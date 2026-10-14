<!--
  The rendering half of ProjectSkillsView.vue, split out by hand: props in, events out.
  The page keeps the requests, the route, the header commands and the delete confirmation.
-->

<script setup lang="ts">
import type { ProjectSkill, ProjectSkillContent, ProjectSkillRevision, SkillImportPreview } from '@/api/projectSkills'

import { computed } from 'vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AppPage from '@/components/common/AppPage.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import SkillDetailDrawer from '@/components/skills/SkillDetailDrawer.vue'
import SkillEditDialog from '@/components/skills/SkillEditDialog.vue'
import SkillImportDialog from '@/components/skills/SkillImportDialog.vue'
import i18n, { t } from '@/i18n'

/** 过了这个数，芝士不再主动提议新的（后端 `PROPOSAL_LIMIT`）；人加不拦，只提一句。 */
const CROWDED = 20

const props = defineProps<{
  loading: boolean
  loadError: string
  skills: ProjectSkill[]
  selectedId: string | null
  selected: ProjectSkill | null
  contents: Record<string, string> | null
  revisions: ProjectSkillRevision[] | null
  busy: string
  detailError: string
  saving: boolean
  formError: string
  preview: SkillImportPreview | null
  reading: boolean
  adding: boolean
  importError: string
}>()

/** 新建 / 修改对话框开在哪一份上（`'new'` 是新建）；页面是这一件事的拥有者。 */
const editing = defineModel<ProjectSkill | 'new' | null>('editing', { required: true })
/** 导入对话框开着没有。 */
const importing = defineModel<boolean>('importing', { required: true })

const emit = defineEmits<{
  open: [id: string]
  close: []
  save: []
  decline: []
  discard: []
  'start-edit': []
  restore: [revision: number]
  delete: []
  'save-form': [value: ProjectSkillContent & { name: string }]
  'read-import': [source: { file: File } | { url: string }]
  'add-import': [value: ProjectSkillContent & { name: string }]
  back: []
}>()

const drafts = computed(() => props.skills.filter((s) => s.state === 'draft'))
const active = computed(() => props.skills.filter((s) => s.state === 'active'))
const taken = computed(() => props.skills.map((s) => s.name))

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString(i18n.global.locale.value, { month: 'long', day: 'numeric' }) : ''
}

function originLabel(s: ProjectSkill): string {
  if (s.origin === 'import') return t('work.skills.list.originImport')
  if (s.origin === 'person') return t('work.skills.list.originPerson')
  return ''
}
</script>

<template>
  <AppPage :title="t('navigation.project.skills')">
    <p v-if="loadError" role="alert" class="t-body c-danger mb-4">{{ loadError }}</p>

    <div v-if="loading && !skills.length" class="py-8 text-center" role="status" :aria-label="t('work.skills.loading')">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>

    <template v-else>
      <section v-if="drafts.length" class="skills-group">
        <h2 class="t-eyebrow c-muted skills-group__head">
          {{ t('work.skills.awaiting') }}<span class="c-faint skills-group__count">{{ drafts.length }}</span>
        </h2>
        <ul class="skills-list">
          <!-- 整行可点；键盘落在名称那颗按钮上。行里提到的人是链接，点它不开详情。 -->
          <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- the pointer's shortcut over the whole row; the row's title button below is the keyboard path (this is the line the page carried before the split) -->
          <li
            v-for="s in drafts"
            :key="s.id"
            class="skills-row"
            :class="{ 'is-selected': s.id === selectedId }"
            :data-skill="s.id"
            @click="emit('open', s.id)"
          >
            <span class="skills-row__main">
              <button type="button" class="t-body skills-row__title" @click.stop="emit('open', s.id)">
                {{ s.title }}
              </button>
              <span class="t-meta c-muted skills-row__use">{{ s.description }}</span>
            </span>
            <span class="t-meta c-faint skills-row__meta" @click.stop>
              <i18n-t
                scope="global"
                :keypath="s.shipped_revision ? 'work.skills.list.draftEdit' : 'work.skills.list.draftNew'"
                tag="span"
              >
                <template #name><UserRef :handle="s.proposed_by" /></template>
              </i18n-t>
              <span>{{ fmt(s.updated_at) }}</span>
            </span>
          </li>
        </ul>
      </section>

      <section v-if="active.length" class="skills-group">
        <h2 class="t-eyebrow c-muted skills-group__head">
          {{ t('work.skills.mine') }}<span class="c-faint skills-group__count">{{ active.length }}</span>
          <span v-if="active.length > CROWDED" class="t-meta c-muted skills-group__note">
            {{ t('work.skills.crowded') }}
          </span>
        </h2>
        <ul class="skills-list">
          <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- the pointer's shortcut over the whole row; the row's title button below is the keyboard path (this is the line the page carried before the split) -->
          <li
            v-for="s in active"
            :key="s.id"
            class="skills-row"
            :class="{ 'is-selected': s.id === selectedId }"
            :data-skill="s.id"
            @click="emit('open', s.id)"
          >
            <span class="skills-row__main">
              <button type="button" class="t-body skills-row__title" @click.stop="emit('open', s.id)">
                {{ s.title }}
              </button>
              <span class="t-meta c-muted skills-row__use">{{ s.description }}</span>
            </span>
            <span class="t-meta c-faint skills-row__meta" @click.stop>
              <span v-if="s.origin === 'cheese'">
                <i18n-t scope="global" keypath="work.skills.list.originCheese" tag="span">
                  <template #name><UserRef :handle="s.proposed_by" /></template>
                </i18n-t>
              </span>
              <span v-else>{{ originLabel(s) }}</span>
              <span>{{
                t('work.skills.list.revision', { revision: s.shipped_revision, date: fmt(s.confirmed_at) })
              }}</span>
            </span>
          </li>
        </ul>
      </section>

      <BaseEmptyState v-if="!skills.length && !loadError" :title="t('work.skills.empty')" />
    </template>

    <SkillDetailDrawer
      :skill="selected"
      :contents="contents"
      :revisions="revisions"
      :busy="busy"
      :error="detailError"
      @close="emit('close')"
      @save="emit('save')"
      @decline="emit('decline')"
      @discard="emit('discard')"
      @edit="emit('start-edit')"
      @delete="emit('delete')"
      @restore="(revision: number) => emit('restore', revision)"
    />

    <SkillEditDialog
      :editing="editing"
      :contents="contents"
      :taken="taken"
      :saving="saving"
      :error="formError"
      @close="editing = null"
      @save="(value) => emit('save-form', value)"
    />

    <SkillImportDialog
      :open="importing"
      :preview="preview"
      :reading="reading"
      :adding="adding"
      :error="importError"
      @close="importing = false"
      @read="(source) => emit('read-import', source)"
      @back="emit('back')"
      @add="(value) => emit('add-import', value)"
    />
  </AppPage>
</template>

<style scoped>
.skills-group + .skills-group {
  margin-top: 24px;
}

.skills-group__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin: 0 0 8px;
}

.skills-group__note {
  font-weight: 400;
}

.skills-list {
  margin: 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--line);
}

.skills-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 8px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.skills-row:hover,
.skills-row.is-selected {
  background: var(--fill);
}

.skills-row__title {
  padding: 0;
  text-align: left;
  background: none;
  border: 0;
  cursor: pointer;
}

.skills-row__title:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

.skills-row__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.skills-row__title {
  align-self: flex-start;
  color: var(--ink);
}

.skills-row__use {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skills-row__meta {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  align-items: flex-end;
  text-align: right;
}

@media (max-width: 599px) {
  .skills-row {
    flex-direction: column;
    align-items: stretch;
    gap: 4px;
  }

  .skills-row__meta {
    flex-direction: row;
    gap: 8px;
    align-items: baseline;
  }
}
</style>
