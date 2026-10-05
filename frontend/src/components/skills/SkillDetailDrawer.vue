<script setup lang="ts">
// 一份技能的详情，从右边滑出来。上面是芝士提议的依据（只有它提议的才有），下面是全文；
// 另一页签是历史版本。动作放在底部：等人确认的那一份是「不保存 / 修改 / 保存」，已保存
// 的是「删除 / 修改」。数据和动作都在页面那一层，这里只画、只发事件。
import type { ProjectSkill, ProjectSkillRevision } from '@/lib/projectSkill'
import type { UserRefTarget } from '@/lib/userRef'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import BaseButton from '@/components/base/BaseButton.vue'
import { MarkdownRenderer } from '@/components/chat/services/markdownRenderer'
import UserRef from '@/components/common/UserRef.vue'
import i18n, { t } from '@/i18n'

const props = defineProps<{
  skill: ProjectSkill | null
  /** 配套文件的内容；还没读到是 null。 */
  contents: Record<string, string> | null
  /** 历史版本；还没读到是 null。 */
  revisions: ProjectSkillRevision[] | null
  /** 正在做的那件事：`save` / `decline` / `discard` / `restore:<n>`。 */
  busy: string
  error: string
  /** 句子里那个人的名字和去处：名册/路由判断在页面那一层（useUserRef 的同一套），
   *  这里只画。 */
  userOf: (handle?: string | null) => { name: string; to: UserRefTarget | null }
}>()

const emit = defineEmits<{
  close: []
  save: []
  decline: []
  discard: []
  edit: []
  delete: []
  restore: [revision: number]
  navigate: [target: UserRefTarget | null]
}>()

const markdown = new MarkdownRenderer()
const { width } = useDisplay()
// 设计值 520，但不超过视口：手机上就是整屏。
const drawerWidth = computed(() => Math.min(520, width.value))

const tab = ref<'content' | 'history'>('content')
const viewing = ref<number | null>(null)
watch(
  () => props.skill?.id,
  () => {
    tab.value = 'content'
    viewing.value = null
  }
)

const s = computed(() => props.skill)
const isDraft = computed(() => s.value?.state === 'draft')
const isEdit = computed(() => isDraft.value && !!s.value?.shipped_revision)
const proposal = computed(() => (isDraft.value ? s.value?.proposal : null))
const body = computed(() => (s.value ? markdown.render(s.value.body) : ''))
const files = computed(() =>
  Object.entries(s.value?.files ?? {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([path, entry]) => ({ path, size: kb(entry.size), text: props.contents?.[path] ?? null }))
)

function render(text: string): string {
  return markdown.render(text)
}

function kb(bytes: number): string {
  return t('work.skills.detail.size', { kb: Math.max(1, Math.round(bytes / 1024)) })
}

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString(i18n.global.locale.value, { month: 'long', day: 'numeric' }) : ''
}
</script>

<template>
  <v-navigation-drawer
    :model-value="!!skill"
    location="right"
    temporary
    :width="drawerWidth"
    @update:model-value="!$event && emit('close')"
  >
    <div v-if="s" class="sdd" :data-skill-detail="s.id">
      <header class="sdd__head">
        <div class="sdd__id">
          <div class="t-meta c-faint sdd__byline">
            <template v-if="isDraft">
              <i18n-t :keypath="isEdit ? 'work.skills.list.draftEdit' : 'work.skills.list.draftNew'" tag="span">
                <template #name>
                  <UserRef
                    :handle="s.proposed_by"
                    :name="userOf(s.proposed_by).name"
                    :to="userOf(s.proposed_by).to"
                    @navigate="$emit('navigate', userOf(s.proposed_by).to)"
                  />
                </template>
              </i18n-t>
            </template>
            <template v-else>
              {{ t('work.skills.list.revision', { revision: s.shipped_revision, date: fmt(s.confirmed_at) }) }}
            </template>
            <span class="sdd__slug">{{ t('work.skills.detail.callName', { name: s.name }) }}</span>
          </div>
          <h2 class="t-title sdd__title">{{ s.title }}</h2>
          <p class="t-body c-muted sdd__use">{{ s.description }}</p>
        </div>
        <BaseButton icon="mdi-close" size="sm" :aria-label="t('work.skills.close')" @click="emit('close')" />
      </header>

      <v-tabs v-model="tab" density="compact" class="sdd__tabs">
        <v-tab value="content">{{ t('work.skills.detail.content') }}</v-tab>
        <v-tab value="history">{{ t('work.skills.history') }}</v-tab>
      </v-tabs>

      <div class="sdd__body">
        <template v-if="tab === 'content'">
          <section v-if="proposal" class="sdd__proposal">
            <template v-if="proposal.reason">
              <h3 class="t-eyebrow c-muted">{{ t('work.skills.proposal.reason') }}</h3>
              <p class="t-body">{{ proposal.reason }}</p>
            </template>
            <template v-if="proposal.taught?.length">
              <h3 class="t-eyebrow c-muted">{{ t('work.skills.proposal.taught') }}</h3>
              <ul class="sdd__taught t-body">
                <li v-for="(line, i) in proposal.taught" :key="i">{{ line }}</li>
              </ul>
            </template>
            <template v-if="proposal.accepted">
              <h3 class="t-eyebrow c-muted">{{ t('work.skills.proposal.accepted') }}</h3>
              <p class="t-body">{{ proposal.accepted }}</p>
            </template>
            <template v-if="proposal.related">
              <h3 class="t-eyebrow c-muted">{{ t('work.skills.proposal.related') }}</h3>
              <p class="t-body">{{ proposal.related }}</p>
            </template>
          </section>

          <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
          <div class="markdown-body sdd__md t-reading" data-user-content v-html="body" />

          <section v-if="files.length" class="sdd__files">
            <h3 class="t-eyebrow c-muted">{{ t('work.skills.fields.files') }}</h3>
            <details v-for="f in files" :key="f.path" class="sdd__file">
              <summary class="t-body">
                <span class="sdd__path">{{ f.path }}</span>
                <span class="t-meta c-faint">{{ f.size }}</span>
              </summary>
              <pre v-if="f.text !== null" class="sdd__pre">{{ f.text }}</pre>
            </details>
          </section>

          <p v-if="proposal?.absorbs?.length" class="t-meta c-muted">
            {{
              t('work.skills.proposal.absorbs', {
                memories: proposal.absorbs.map((m) => m.title).join(t('work.skills.listSeparator')),
              })
            }}
          </p>
        </template>

        <template v-else>
          <div v-if="revisions === null" class="py-6 text-center" role="status">
            <v-progress-circular indeterminate size="24" color="primary" />
          </div>
          <ol v-else class="sdd__revisions">
            <li v-for="r in revisions" :key="r.revision" class="sdd__revision" :data-revision="r.revision">
              <div class="sdd__revision-head">
                <div class="t-meta">
                  <i18n-t keypath="work.skills.revisionLine" tag="span">
                    <template #revision>{{ r.revision }}</template>
                    <template #note>{{ r.note }}</template>
                    <template #user>
                      <UserRef
                        :handle="r.confirmed_by"
                        :name="userOf(r.confirmed_by).name"
                        :to="userOf(r.confirmed_by).to"
                        @navigate="$emit('navigate', userOf(r.confirmed_by).to)"
                      />
                    </template>
                    <template #time>{{ fmt(r.created_at) }}</template>
                  </i18n-t>
                </div>
                <v-chip v-if="r.revision === s.shipped_revision" size="x-small" color="success" variant="tonal">
                  {{ t('work.skills.inUse') }}
                </v-chip>
              </div>
              <div class="sdd__revision-actions">
                <BaseButton size="sm" @click="viewing = viewing === r.revision ? null : r.revision">
                  {{ viewing === r.revision ? t('work.skills.collapse') : t('work.skills.view') }}
                </BaseButton>
                <BaseButton
                  v-if="r.revision !== s.shipped_revision"
                  size="sm"
                  :loading="busy === `restore:${r.revision}`"
                  @click="emit('restore', r.revision)"
                >
                  {{ t('work.skills.restore') }}
                </BaseButton>
              </div>
              <div v-if="viewing === r.revision" class="sdd__old">
                <p class="t-body c-muted">{{ r.content.description }}</p>
                <!-- eslint-disable-next-line vue/no-v-html -- sanitized by MarkdownRenderer (DOMPurify) -->
                <div class="markdown-body sdd__md t-reading" data-user-content v-html="render(r.content.body)" />
              </div>
            </li>
          </ol>
        </template>
      </div>

      <footer class="sdd__foot">
        <p v-if="error" role="alert" class="t-body c-danger sdd__error">{{ error }}</p>
        <template v-if="isDraft">
          <BaseButton v-if="isEdit" :loading="busy === 'discard'" data-action="discard" @click="emit('discard')">
            {{ t('work.skills.discard') }}
          </BaseButton>
          <BaseButton v-else :loading="busy === 'decline'" data-action="decline" @click="emit('decline')">
            {{ t('work.skills.proposal.decline') }}
          </BaseButton>
          <v-spacer />
          <BaseButton kind="secondary" :disabled="contents === null" data-action="edit" @click="emit('edit')">{{
            t('work.skills.edit')
          }}</BaseButton>
          <BaseButton kind="primary" :loading="busy === 'save'" data-action="save" @click="emit('save')">
            {{ t('work.skills.save') }}
          </BaseButton>
        </template>
        <template v-else>
          <BaseButton kind="danger" data-action="delete" @click="emit('delete')">
            {{ t('work.skills.delete') }}
          </BaseButton>
          <v-spacer />
          <BaseButton kind="secondary" :disabled="contents === null" data-action="edit" @click="emit('edit')">{{
            t('work.skills.edit')
          }}</BaseButton>
        </template>
      </footer>
    </div>
  </v-navigation-drawer>
</template>

<style scoped>
.sdd {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.sdd__head {
  display: flex;
  flex: 0 0 auto;
  align-items: flex-start;
  gap: 8px;
  padding: 16px 8px 8px 16px;
}

.sdd__id {
  flex: 1;
  min-width: 0;
}

.sdd__byline {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
}

.sdd__slug {
  font-family: var(--font-mono, monospace);
}

.sdd__title {
  margin: 4px 0 0;
  color: var(--ink);
}

.sdd__use {
  margin: 4px 0 0;
}

.sdd__tabs {
  flex: 0 0 auto;
  padding: 0 8px;
  border-bottom: 1px solid var(--line);
}

.sdd__body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 16px;
  min-height: 0;
  padding: 16px;
  overflow-y: auto;
}

.sdd__proposal {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.sdd__proposal h3 {
  margin: 4px 0 0;
}

.sdd__proposal h3:first-child {
  margin-top: 0;
}

.sdd__proposal p {
  margin: 0;
  overflow-wrap: anywhere;
}

.sdd__taught {
  margin: 0;
  padding-left: 18px;
}

.sdd__md {
  overflow-wrap: anywhere;
}

.sdd__md :deep(h1),
.sdd__md :deep(h2),
.sdd__md :deep(h3) {
  margin: 16px 0 6px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.sdd__md :deep(h1:first-child),
.sdd__md :deep(h2:first-child),
.sdd__md :deep(h3:first-child) {
  margin-top: 0;
}

.sdd__md :deep(p),
.sdd__md :deep(ul),
.sdd__md :deep(ol) {
  margin: 0 0 8px;
}

.sdd__md :deep(ul),
.sdd__md :deep(ol) {
  padding-left: 20px;
}

.sdd__files {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sdd__files h3 {
  margin: 0 0 4px;
}

.sdd__file summary {
  display: flex;
  gap: 8px;
  align-items: baseline;
  cursor: pointer;
}

.sdd__path {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  font-family: var(--font-mono, monospace);
}

.sdd__pre {
  margin: 8px 0;
  padding: 12px;
  overflow-x: auto;
  background: var(--fill);
  border-radius: var(--radius-md);
  color: var(--text);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: pre;
}

.sdd__revisions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.sdd__revision {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.sdd__revision-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.sdd__revision-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}

.sdd__old {
  margin-top: 8px;
}

.sdd__old p {
  margin: 0 0 8px;
}

.sdd__foot {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
}

.sdd__error {
  flex: 1 0 100%;
  margin: 0;
}
</style>
