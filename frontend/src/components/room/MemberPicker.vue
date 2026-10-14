<script setup lang="ts">
// 往频道里加人那一步的「挑人」：可搜索、可多选，勾好的人挂在上面当可移除的头像 chip。
// 纯 props —— 候选名单、勾了谁、忙不忙都由调用方给；这一件只画、只改「勾了谁」，
// 一个请求都不发（发请求、怎么发、失败了留谁，是页面的事，见 TopicMembers）。
//
// 名单空着（名册还没读到）时列不出任何人，也就挑不了 —— 这正是要的：不先知道房间里
// 已经有谁，就不该让人挑。
import type { MemberCandidate } from '@/lib/memberPicker'

import { computed, ref } from 'vue'

import ExternalTag from '../common/ExternalTag.vue'
import IdentityView from '../common/IdentityView.vue'

import { t } from '@/i18n'
import { filterCandidates, toggleCandidate } from '@/lib/memberPicker'

const props = withDefaults(
  defineProps<{
    /** 候选：还没在这间房里的项目成员。 */
    options: MemberCandidate[]
    /** 勾了谁（handle）。 */
    modelValue: string[]
    /** 忙着（正在送请求）：这一件整个不可改。 */
    disabled?: boolean
    /** 搜索框里的提示语。 */
    placeholder?: string
  }>(),
  { disabled: false, placeholder: undefined }
)
const emit = defineEmits<{ (e: 'update:modelValue', value: string[]): void }>()

const query = ref('')

// 勾了谁：按 handle 找回候选那一行（拿它的名字、头像、标）。勾过又不在候选里的
// （名单换了）不画 —— 不认得就别凭空画一个。
const chosen = computed(() => {
  const byHandle = new Map(props.options.map((o) => [o.handle, o]))
  return props.modelValue.map((h) => byHandle.get(h)).filter((o): o is MemberCandidate => o !== undefined)
})
const shown = computed(() => filterCandidates(props.options, query.value))

function isChosen(handle: string): boolean {
  return props.modelValue.includes(handle)
}
function toggle(handle: string) {
  if (props.disabled) return
  emit('update:modelValue', toggleCandidate(props.modelValue, handle))
}
</script>

<template>
  <div class="member-picker">
    <!-- Who is picked: one removable avatar chip each, read before hitting Add. -->
    <ul v-if="chosen.length" class="member-picker__chips">
      <li v-for="c in chosen" :key="c.handle" class="member-picker__chip">
        <IdentityView :handle="c.handle" :name="c.name" :avatar-id="c.avatarId" :agent="c.agent" :size="18" />
        <button
          type="button"
          class="member-picker__unpick"
          :disabled="disabled"
          :title="t('work.room.roster.unpick')"
          :aria-label="t('work.room.roster.unpickName', { name: c.name })"
          @click="toggle(c.handle)"
        >
          <v-icon size="14">mdi-close</v-icon>
        </button>
      </li>
    </ul>

    <v-text-field
      v-model="query"
      class="member-picker__search"
      density="compact"
      variant="outlined"
      hide-details
      autocomplete="off"
      :disabled="disabled"
      :placeholder="placeholder ?? t('work.room.roster.pickPlaceholder')"
      :aria-label="t('work.room.roster.pickLabel')"
      prepend-inner-icon="mdi-magnify"
    />

    <!-- Candidates: each row looks like a roster row (avatar, name, @handle, tag). -->
    <ul class="member-picker__list">
      <li v-for="o in shown" :key="o.handle">
        <button
          type="button"
          class="member-picker__row"
          :class="{ 'member-picker__row--on': isChosen(o.handle) }"
          :aria-pressed="isChosen(o.handle)"
          :disabled="disabled"
          @click="toggle(o.handle)"
        >
          <IdentityView
            :handle="o.handle"
            :name="o.name"
            :avatar-id="o.avatarId"
            :agent="o.agent"
            :sub-handle="o.sub"
            :size="26"
            handle-line
          />
          <span v-if="o.agent" class="member-picker__badge">{{ t('work.room.roster.agentBadge') }}</span>
          <ExternalTag v-else-if="o.external" />
          <v-icon v-if="isChosen(o.handle)" class="member-picker__tick" size="18">mdi-check</v-icon>
        </button>
      </li>
      <li v-if="!shown.length" class="member-picker__empty">{{ t('work.room.roster.noMatch') }}</li>
    </ul>
  </div>
</template>

<style scoped>
.member-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
/* 勾了谁：一行可换行的 chip，每个是「头像 + 名字 + ×」。 */
.member-picker__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  list-style: none;
  margin: 0;
  padding: 0;
}
.member-picker__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px 2px 6px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  background: var(--surface);
  /* 名字很长的 chip 在手机上要能缩：不能顶破一行 —— 名字本身在 IdentityView 里
     已经是省略号，这里给它一条能缩的上限。 */
  min-width: 0;
  max-width: 100%;
}
.member-picker__unpick {
  display: inline-flex;
  align-items: center;
  color: var(--muted);
  cursor: pointer;
  flex: none;
}
.member-picker__unpick:hover:not(:disabled) {
  color: var(--ink);
}
.member-picker__unpick:disabled {
  opacity: 0.4;
  cursor: default;
}
.member-picker__search {
  flex: none;
}
.member-picker__list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 240px;
  overflow-y: auto;
}
.member-picker__row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  text-align: start;
  cursor: pointer;
  min-width: 0;
}
.member-picker__row:hover:not(:disabled) {
  background: var(--fill);
}
/* 选中只说明「勾上了」，不是它在喊：中性的一条底边，不拿主题色/琥珀色去装饰选项。 */
.member-picker__row--on {
  background: var(--line-2);
}
.member-picker__row:disabled {
  opacity: 0.6;
  cursor: default;
}
.member-picker__badge {
  font-size: 13px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  background: var(--fill);
  flex: none;
}
.member-picker__tick {
  margin-inline-start: auto;
  color: var(--muted);
  flex: none;
}
.member-picker__empty {
  padding: 10px 8px;
  font-size: 13px;
  color: var(--muted);
}
</style>
