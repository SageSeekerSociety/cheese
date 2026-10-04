<script setup lang="ts">
import type { ProjectSkill } from '@/lib/projectSkill'

import { ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import FirstTimeHint from '@/components/common/FirstTimeHint.vue'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

// 房间里请人保存技能的那张卡：芝士把一套做法整理成技能（或者提议改一份），
// 人就在说话的地方决定存不存，不用跳去技能页。
//
// 卡上最显眼的是**依据**，不是技能本身：用户教过它哪几处、这次怎么算做成的、和已有
// 技能比过没有。一项技能会被之后每个会话整套照做，确认的人要判断的是「这是不是我们
// 的做法」，那得先看见它从哪来。全文默认收起。
//
// 「不保存」和「保存」一样容易点到。拒绝的新技能服务端记着，芝士不会再提同一份；拒绝的
// 改动退回保存的那一版。取数和保存在 `useSkillProposals`，这里只画。
defineOptions({ name: 'SkillProposalCard' })

defineProps<{
  /** 还在等人的提议。 */
  proposals: ProjectSkill[]
  /** 这一轮刚保存的。 */
  saved: ProjectSkill[]
  /** 正在保存或拒绝的那一份的 id。 */
  busy: string
  error: string
}>()

const emit = defineEmits<{
  save: [skill: ProjectSkill]
  decline: [skill: ProjectSkill]
  open: [skill: ProjectSkill]
}>()

const workspace = useWorkspaceStore()
const expanded = ref<Set<string>>(new Set())

function nameOf(handle: string): string {
  return workspace.members.find((m) => m.user_handle === handle)?.name || handle
}

function toggle(id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}
</script>

<template>
  <div v-for="s in saved" :key="`saved-${s.id}`" class="method-card mt-2">
    <div class="method-card__pad d-flex align-center flex-wrap ga-2">
      <v-icon color="success" size="19">mdi-check-circle-outline</v-icon>
      <span class="t-body">{{ t('work.skills.proposal.saved', { title: s.title }) }}</span>
      <v-spacer />
      <BaseButton kind="secondary" size="sm" @click="emit('open', s)">{{ t('work.skills.proposal.open') }}</BaseButton>
    </div>
  </div>

  <div v-for="s in proposals" :key="s.id" class="method-card mt-2">
    <div class="method-card__pad">
      <div class="t-meta-read mb-1">
        {{
          s.shipped_revision
            ? t('work.skills.proposal.bylineEdit', { name: nameOf(s.proposed_by) })
            : t('work.skills.proposal.byline', { name: nameOf(s.proposed_by) })
        }}
      </div>
      <div class="t-title mb-1">{{ s.title }}</div>
      <div class="t-body method-card__text mb-3">{{ s.description }}</div>

      <div v-if="s.proposal?.reason" class="method-card__said mb-3">
        <div class="t-eyebrow mb-1">{{ t('work.skills.proposal.reason') }}</div>
        <div class="t-body method-card__text">{{ s.proposal.reason }}</div>
      </div>
      <div v-if="s.proposal?.taught?.length" class="method-card__said mb-3">
        <div class="t-eyebrow mb-1">{{ t('work.skills.proposal.taught') }}</div>
        <ul class="method-card__list t-body">
          <li v-for="(line, i) in s.proposal.taught" :key="i">{{ line }}</li>
        </ul>
      </div>
      <div v-if="s.proposal?.accepted || s.proposal?.related" class="method-card__reason mb-3">
        <template v-if="s.proposal?.accepted">
          <div class="t-eyebrow mb-1">{{ t('work.skills.proposal.accepted') }}</div>
          <div class="t-body method-card__text">{{ s.proposal.accepted }}</div>
        </template>
        <template v-if="s.proposal?.related">
          <div class="t-eyebrow mb-1" :class="{ 'mt-2': s.proposal?.accepted }">
            {{ t('work.skills.proposal.related') }}
          </div>
          <div class="t-body method-card__text">{{ s.proposal.related }}</div>
        </template>
      </div>
      <div v-if="s.proposal?.absorbs?.length" class="t-meta mb-3">
        {{
          t('work.skills.proposal.absorbs', {
            memories: s.proposal.absorbs.map((m) => m.title).join(t('work.skills.listSeparator')),
          })
        }}
      </div>

      <!-- `|| undefined`：inert 只看属性在不在，`inert="false"` 照样让整块读不到。 -->
      <div class="method-card__fold" :class="{ 'is-open': expanded.has(s.id) }">
        <div class="method-card__fold-inner" :inert="!expanded.has(s.id) || undefined">
          <dl class="method-card__body">
            <template v-if="s.inputs">
              <dt class="t-eyebrow">{{ t('work.skills.fields.inputs') }}</dt>
              <dd class="t-body method-card__text">{{ s.inputs }}</dd>
            </template>
            <dt class="t-eyebrow">{{ t('work.skills.fields.steps') }}</dt>
            <dd class="t-body method-card__text">{{ s.steps }}</dd>
            <template v-if="s.outputs">
              <dt class="t-eyebrow">{{ t('work.skills.fields.outputs') }}</dt>
              <dd class="t-body method-card__text">{{ s.outputs }}</dd>
            </template>
            <template v-if="Object.keys(s.files).length">
              <dt class="t-eyebrow">{{ t('work.skills.fields.files') }}</dt>
              <dd class="t-body">{{ Object.keys(s.files).join(t('work.skills.listSeparator')) }}</dd>
            </template>
          </dl>
        </div>
      </div>

      <FirstTimeHint id="skill-proposal">{{ t('global.firstHint.skillProposal') }}</FirstTimeHint>
      <div v-if="error" class="t-meta method-card__error mb-2" role="alert">{{ error }}</div>
      <div class="d-flex align-center flex-wrap ga-2">
        <BaseButton
          kind="ghost"
          size="sm"
          :prepend-icon="expanded.has(s.id) ? 'mdi-chevron-up' : 'mdi-chevron-down'"
          @click="toggle(s.id)"
        >
          {{ expanded.has(s.id) ? t('work.skills.proposal.collapse') : t('work.skills.proposal.expand') }}
        </BaseButton>
        <BaseButton kind="ghost" size="sm" :disabled="busy === s.id" @click="emit('decline', s)">
          {{ t('work.skills.proposal.decline') }}
        </BaseButton>
        <v-spacer />
        <BaseButton kind="primary" size="sm" :loading="busy === s.id" @click="emit('save', s)">
          {{ t('work.skills.save') }}
        </BaseButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.method-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  color: var(--ink);
}
.method-card__pad {
  padding: 12px;
}
.method-card__text {
  white-space: pre-wrap;
}
/* 用户教过的话是证据，左边一道竖线引用；芝士自己的判断用 inset 底色，两者不该长得一样。 */
.method-card__said {
  padding: 8px 10px 8px 12px;
  border-left: 2px solid var(--line-2);
}
.method-card__reason {
  padding: 8px 10px;
  border-radius: var(--radius-md);
  background: var(--fill);
}
.method-card__list {
  margin: 0;
  padding-left: 18px;
}
.method-card__fold {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--dur-base) var(--ease-standard);
}
.method-card__fold.is-open {
  grid-template-rows: 1fr;
}
.method-card__fold-inner {
  min-height: 0;
  overflow: hidden;
}
.method-card__body {
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.method-card__body dd {
  margin: 2px 0 10px;
}
.method-card__body dd:last-child {
  margin-bottom: 0;
}
.method-card__error {
  color: var(--danger);
}
@media (prefers-reduced-motion: reduce) {
  .method-card__fold {
    transition: none;
  }
}
</style>
