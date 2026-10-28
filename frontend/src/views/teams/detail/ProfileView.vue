<!--
  团队设置的「基本信息」：头像、名称、团队地址、简介，和跟着它们的那条保存栏。只吃
  props：存、传头像、换地址都在页面（`Profile.vue`），这里画草稿、报按了哪一颗。
-->
<template>
  <div class="settings-page">
    <header class="settings-page__head">
      <div>
        <h1 class="t-page-title">{{ t('teams.settings.profile') }}</h1>
        <p class="settings-page__lede">{{ t('teams.settings.profileLede') }}</p>
      </div>
    </header>

    <p v-if="!canEdit" class="t-body c-muted">{{ t('work.teamProfile.forbidden') }}</p>

    <form class="settings-card" novalidate @submit.prevent="submit">
      <div class="srow srow--field">
        <span class="srow__k">{{ t('work.teamProfile.avatarLabel') }}</span>
        <div class="avatar-field">
          <UserAvatar :avatar="shownAvatar" :name="team.name" :seed="team.handle" size="64" kind="org" />
          <div v-if="canEdit" class="avatar-field__side">
            <BaseButton kind="secondary" :disabled="saving" @click="avatarInput?.click()">
              {{ t('account.profile.changeAvatar') }}
            </BaseButton>
            <span class="field-note">{{ t('teams.settings.avatarHint') }}</span>
          </div>
          <input
            ref="avatarInput"
            class="avatar-field__input"
            type="file"
            :accept="avatarAccept"
            :aria-label="t('account.profile.changeAvatar')"
            @change="picked"
          />
        </div>
      </div>

      <div class="srow srow--field">
        <label id="team-name-label" class="srow__k" for="team-name">{{ t('work.teamProfile.nameLabel') }}</label>
        <v-text-field
          id="team-name"
          v-model="name"
          autocomplete="off"
          variant="outlined"
          density="compact"
          :disabled="!canEdit"
          :error-messages="nameError"
          hide-details="auto"
        />
      </div>

      <div class="srow srow--field">
        <label id="team-handle-label" class="srow__k" for="team-handle">{{ t('work.teamLink.address') }}</label>
        <v-text-field
          id="team-handle"
          v-model="handle"
          autocomplete="off"
          variant="outlined"
          density="compact"
          :prefix="addressPrefix"
          :disabled="!canEdit"
          :hint="t('work.teamLink.addressHint')"
          persistent-hint
          :error-messages="handleError"
        />
      </div>

      <div class="srow srow--field">
        <label id="team-intro-label" class="srow__k" for="team-intro">{{ t('work.teamProfile.introLabel') }}</label>
        <v-textarea
          id="team-intro"
          v-model="intro"
          autocomplete="off"
          variant="outlined"
          density="compact"
          rows="2"
          auto-grow
          no-resize
          :disabled="!canEdit"
          :counter="INTRO_MAX"
          persistent-counter
        />
      </div>

      <SaveBar
        v-if="canEdit"
        :dirty="dirty"
        :saving="saving"
        :saved="saved"
        :error="error"
        :disabled="!name.trim() || !handle.trim()"
        :note="t('teams.settings.unsaved')"
        :revert-label="t('teams.settings.revert')"
        :save-label="t('work.teamProfile.save')"
        @revert="revert"
        @save="submit"
      />
    </form>
  </div>
</template>

<script setup lang="ts">
// 四栏是这一页自己的草稿：在这儿改，脏了才交给页面去存。头像选好先只在这里预览，
// 和另外三栏一起存。
import type { Team } from '@/types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import SaveBar from '@/components/base/SaveBar.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

const INTRO_MAX = 250

const props = defineProps<{
  team: Team
  /** 现在的头像地址；没挑过是空串。 */
  avatar: string
  /** 所有者和管理员能改；别人只看。 */
  canEdit: boolean
  /** 地址那一格前面那一截（`okcheese.com/teams/`）。 */
  addressPrefix: string
  /** 文件框收哪几种图，逗号分隔。 */
  avatarAccept: string
  saving: boolean
  saved: boolean
  error: string
  nameError: string
  handleError: string
}>()

const emit = defineEmits<{
  save: [payload: { name: string; intro: string; handle: string; avatarFile?: File }]
  /** 人动了某一格：页面把上一次的报错撤掉。 */
  edited: []
}>()

const name = ref('')
const intro = ref('')
const handle = ref('')
const avatarFile = ref<File>()
const preview = ref('')
const avatarInput = ref<HTMLInputElement | null>(null)

function revert() {
  name.value = props.team.name
  intro.value = props.team.intro ?? ''
  handle.value = props.team.handle
  avatarFile.value = undefined
  if (preview.value) URL.revokeObjectURL(preview.value)
  preview.value = ''
}

// 换了一个团队、或者刚存好：草稿从团队现在的样子重新起手。
watch(() => [props.team.id, props.team.name, props.team.intro, props.team.handle, props.team.avatarId], revert, {
  immediate: true,
})
watch([name, intro, handle], () => emit('edited'))
onBeforeUnmount(() => {
  if (preview.value) URL.revokeObjectURL(preview.value)
})

const shownAvatar = computed(() => preview.value || props.avatar)

const dirty = computed(
  () =>
    !!avatarFile.value ||
    name.value !== props.team.name ||
    intro.value !== (props.team.intro ?? '') ||
    handle.value.trim() !== props.team.handle
)

function picked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  avatarFile.value = file
  if (preview.value) URL.revokeObjectURL(preview.value)
  preview.value = URL.createObjectURL(file)
}

function submit() {
  if (!props.canEdit || props.saving || !dirty.value) return
  emit('save', { name: name.value, intro: intro.value, handle: handle.value.trim(), avatarFile: avatarFile.value })
}
</script>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
/* 一行放一格输入：标签对着输入框的第一行，和个人资料那一页同一个样子。 */
.srow--field {
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
  padding: 20px 24px;
}

.srow--field > .srow__k {
  padding-top: 10px;
}

@media (max-width: 767.98px) {
  .srow--field {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
    padding: 16px;
  }

  .srow--field > .srow__k {
    padding-top: 0;
  }
}

.avatar-field {
  display: flex;
  gap: 16px;
  align-items: center;
}

.avatar-field__side {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
}

.avatar-field__input {
  display: none;
}

.field-note {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
