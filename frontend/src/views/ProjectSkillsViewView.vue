<script setup lang="ts">
// 技能：这个项目自己的技能，芝士整理的、手写的、导入的。保存过的那一版会带进之后每个
// 会话，所以芝士整理出来、或者改过的，都要人读一遍、点保存才算数。平台自带的不在这里：
// 那是平台写给芝士的说明，属于平台本身。
//
// 列表铺满，点一行从右边滑出详情；新建是手写，导入只给项目管理员。
//
// 画的那一半：两组列表 + 详情抽屉、编辑框、导入框、删除确认。所有请求、打开哪一份时
// 读的配套文件/历史、选「新建」还是「导入」都在容器 ProjectSkillsView.vue 里 ——
// 这里只收 props，只发事件。
import type { ProjectSkill, ProjectSkillContent, ProjectSkillRevision, SkillImportPreview } from '@/lib/projectSkill'
import type { UserRefTarget } from '@/lib/userRef'

import { computed } from 'vue'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AppPage from '@/components/common/AppPage.vue'
import UserRef from '@/components/common/UserRef.vue'
import SkillDetailDrawer from '@/components/skills/SkillDetailDrawer.vue'
import SkillEditDialog from '@/components/skills/SkillEditDialog.vue'
import SkillImportDialog from '@/components/skills/SkillImportDialog.vue'
import i18n, { t } from '@/i18n'

/** 过了这个数，芝士不再主动提议新的（后端 `PROPOSAL_LIMIT`）；人加不拦，只提一句。 */
const CROWDED = 20

const props = defineProps<{
  skills: ProjectSkill[]
  loading: boolean
  loadError: string
  /** 打开的那一份（详情抽屉）；null 是关着。 */
  selectedId: string | null
  /** 配套文件的内容；还没读到是 null。 */
  contents: Record<string, string> | null
  /** 历史版本；还没读到是 null。 */
  revisions: ProjectSkillRevision[] | null
  /** 正在做的那件事：`save` / `decline` / `discard` / `restore:<n>`。 */
  busy: string
  detailError: string
  /** `'new'` 是新建；一份技能是修改它；null 是关着。 */
  editing: ProjectSkill | 'new' | null
  saving: boolean
  formError: string
  importing: boolean
  preview: SkillImportPreview | null
  reading: boolean
  adding: boolean
  importError: string
  deleteOpen: boolean
  deleteTitle: string
  /** 一句话里那个人的名字和去处：名册/路由判断在容器那边（useUserRef 的同一套）。 */
  userOf: (handle?: string | null) => { name: string; to: UserRefTarget | null }
}>()

defineEmits<{
  open: [id: string]
  close: []
  save: []
  decline: []
  discard: []
  edit: []
  delete: []
  restore: [revision: number]
  navigate: [target: UserRefTarget | null]
  'save-form': [value: ProjectSkillContent & { name: string }]
  'update:editing': [value: ProjectSkill | 'new' | null]
  'import-read': [source: { file: File } | { url: string }]
  'import-back': []
  'import-add': [value: ProjectSkillContent & { name: string }]
  'update:importing': [value: boolean]
  'update:delete-open': [value: boolean]
  'confirm-delete': []
}>()

const drafts = computed(() => props.skills.filter((s) => s.state === 'draft'))
const active = computed(() => props.skills.filter((s) => s.state === 'active'))
const selected = computed(() => props.skills.find((s) => s.id === props.selectedId) ?? null)
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
          <li
            v-for="s in drafts"
            :key="s.id"
            class="skills-row"
            :class="{ 'is-selected': s.id === selectedId }"
            :data-skill="s.id"
            @click="$emit('open', s.id)"
          >
            <span class="skills-row__main">
              <button type="button" class="t-body skills-row__title" @click.stop="$emit('open', s.id)">
                {{ s.title }}
              </button>
              <span class="t-meta c-muted skills-row__use">{{ s.description }}</span>
            </span>
            <span class="t-meta c-faint skills-row__meta" @click.stop>
              <i18n-t
                :keypath="s.shipped_revision ? 'work.skills.list.draftEdit' : 'work.skills.list.draftNew'"
                tag="span"
              >
                <template #name>
                  <UserRef
                    :handle="s.proposed_by"
                    :name="userOf(s.proposed_by).name"
                    :to="userOf(s.proposed_by).to"
                    @navigate="$emit('navigate', userOf(s.proposed_by).to)"
                  />
                </template>
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
          <li
            v-for="s in active"
            :key="s.id"
            class="skills-row"
            :class="{ 'is-selected': s.id === selectedId }"
            :data-skill="s.id"
            @click="$emit('open', s.id)"
          >
            <span class="skills-row__main">
              <button type="button" class="t-body skills-row__title" @click.stop="$emit('open', s.id)">
                {{ s.title }}
              </button>
              <span class="t-meta c-muted skills-row__use">{{ s.description }}</span>
            </span>
            <span class="t-meta c-faint skills-row__meta" @click.stop>
              <span v-if="s.origin === 'cheese'">
                <i18n-t keypath="work.skills.list.originCheese" tag="span">
                  <template #name>
                    <UserRef
                      :handle="s.proposed_by"
                      :name="userOf(s.proposed_by).name"
                      :to="userOf(s.proposed_by).to"
                      @navigate="$emit('navigate', userOf(s.proposed_by).to)"
                    />
                  </template>
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

      <p v-if="!skills.length && !loadError" class="t-body c-muted py-8 text-center">{{ t('work.skills.empty') }}</p>
    </template>

    <SkillDetailDrawer
      :skill="selected"
      :contents="contents"
      :revisions="revisions"
      :busy="busy"
      :error="detailError"
      :user-of="userOf"
      @close="$emit('close')"
      @save="$emit('save')"
      @decline="$emit('decline')"
      @discard="$emit('discard')"
      @edit="$emit('edit')"
      @delete="$emit('delete')"
      @restore="$emit('restore', $event)"
      @navigate="$emit('navigate', $event)"
    />

    <SkillEditDialog
      :editing="editing"
      :contents="contents"
      :taken="taken"
      :saving="saving"
      :error="formError"
      @close="$emit('update:editing', null)"
      @save="$emit('save-form', $event)"
    />

    <SkillImportDialog
      :open="importing"
      :preview="preview"
      :reading="reading"
      :adding="adding"
      :error="importError"
      @close="$emit('update:importing', false)"
      @read="$emit('import-read', $event)"
      @back="$emit('import-back')"
      @add="$emit('import-add', $event)"
    />

    <ConfirmDialog
      :model-value="deleteOpen"
      :title="deleteTitle"
      :confirm-label="t('work.skills.delete')"
      danger
      @update:model-value="$emit('update:delete-open', $event)"
      @confirm="$emit('confirm-delete')"
    >
      {{ t('work.skills.deleteHint') }}
    </ConfirmDialog>
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
