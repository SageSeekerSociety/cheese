<!--
  The profile half of the account settings (Profile.vue): the avatar row, the
  nickname and the intro, and the save bar that goes with them. Props only —
  the page keeps the request, the record and the upload; this draws what it is
  handed and says which button was pressed.
-->
<template>
  <div class="settings-page">
    <header class="profile__head">
      <div>
        <h1 class="t-page-title">{{ t('account.profile.title') }}</h1>
        <p class="settings-page__lede">{{ t('account.profile.lede') }}</p>
      </div>
      <NavLink v-if="user" class="profile__home" :to="{ name: 'UserPage', params: { handle: user.username } }">
        {{ t('account.profile.viewPage') }}
        <v-icon icon="mdi-chevron-right" size="16" />
      </NavLink>
    </header>

    <!-- Save is the page's one main action, so it is the only amber on it
         (design-system §1.6); it exists only while there is something to save. -->
    <form v-if="user" class="settings-card" novalidate @submit.prevent="submit">
      <div class="srow srow--field">
        <span class="srow__k">{{ t('account.profile.avatar') }}</span>
        <div class="avatar-field">
          <UserAvatar class="avatar-field__img" :avatar="shownAvatar" :name="avatarSeed" size="64" />
          <div class="avatar-field__side">
            <div class="avatar-field__actions">
              <BaseButton kind="secondary" :loading="changingAvatar" :disabled="removingAvatar" @click="pick">
                {{ t('account.profile.changeAvatar') }}
              </BaseButton>
              <BaseButton
                v-if="canRemoveAvatar"
                :loading="removingAvatar"
                :disabled="changingAvatar"
                @click="emit('remove-avatar')"
              >
                {{ t('account.profile.removeAvatar') }}
              </BaseButton>
            </div>
            <span class="field-note">{{ t('account.profile.avatarHint') }}</span>
          </div>
          <input
            ref="avatarInput"
            class="avatar-field__input"
            type="file"
            :accept="avatarAccept"
            @change="onAvatarPicked"
          />
        </div>
      </div>

      <div class="srow srow--field">
        <label class="srow__k" for="profile-nickname">{{ t('account.profile.nickname') }}</label>
        <v-text-field
          id="profile-nickname"
          v-model="nickname"
          autocomplete="nickname"
          variant="outlined"
          density="compact"
          :error-messages="nicknameError"
          hide-details="auto"
        />
      </div>

      <div class="srow srow--field">
        <span class="srow__k">{{ t('account.profile.username') }}</span>
        <div class="srow__stack">
          <span class="handle">{{ user.username }}</span>
          <span class="field-note">{{ t('account.profile.usernameNote') }}</span>
        </div>
      </div>

      <div class="srow srow--field">
        <label class="srow__k" for="profile-intro">{{ t('account.profile.intro') }}</label>
        <v-textarea
          id="profile-intro"
          v-model="intro"
          autocomplete="off"
          variant="outlined"
          density="compact"
          rows="2"
          auto-grow
          no-resize
          :counter="INTRO_MAX"
          :counter-value="length"
          persistent-counter
          :error-messages="introError"
        />
      </div>

      <SaveBar
        :dirty="dirty"
        :saving="saving"
        :saved="saved"
        :error="error"
        :disabled="!valid"
        :note="t('account.profile.unsaved')"
        :revert-label="t('account.profile.revert')"
        :save-label="t('account.profile.save')"
        @revert="revert"
        @save="submit"
      />
    </form>
  </div>
</template>

<script setup lang="ts">
// 昵称和简介是这一页自己的草稿：在这儿改、在这儿算错对，脏了才交回给容器去存。
// 容器只关心「存什么」和「存得怎么样」。
import type { User } from '@/types/users'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import SaveBar from '@/components/base/SaveBar.vue'
import NavLink from '@/components/common/NavLink.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

// The server serves four image types as images and anything else as an opaque
// file, which would show as a broken avatar: the page keeps that list and hands
// it down as `avatarAccept`. It sets no size limit of its own.
const INTRO_MAX = 60
const NICKNAME_MAX = 50
// A nickname needs at least one CJK character, letter or digit, so it cannot be
// only symbols or spaces.
const READABLE = /[0-9A-Za-z㐀-䶿一-鿿]/

const props = defineProps<{
  user: User | null
  shownAvatar: string
  avatarSeed: string
  canRemoveAvatar: boolean
  changingAvatar: boolean
  removingAvatar: boolean
  /** 文件框收哪几种图，逗号分隔（服务端按这四种当图片）。 */
  avatarAccept: string
  /** 已存的那两栏；草稿跟着它走，只有没人动过的那一栏才跟。 */
  savedNickname: string
  savedIntro: string
  saving: boolean
  saved: boolean
  error: string
}>()

const emit = defineEmits<{
  /** 把两栏存上去。值已经是校验过的、昵称去过首尾空白。 */
  save: [change: { nickname: string; intro: string }]
  /** 头像被选中：怎么验、怎么传、怎么错，容器的事。 */
  'avatar-picked': [file: File]
  'remove-avatar': []
}>()

const nickname = ref(props.savedNickname)
const intro = ref(props.savedIntro)

// The record can arrive or change after the page opens. A field follows it
// only while nobody has edited that field.
watch(
  () => props.savedNickname,
  (next, prev) => {
    if (nickname.value === prev) nickname.value = next
  }
)
watch(
  () => props.savedIntro,
  (next, prev) => {
    if (intro.value === prev) intro.value = next
  }
)

// Counted in characters, as the server counts them.
const length = (value: string) => [...value].length

const nicknameError = computed(() => {
  const value = nickname.value.trim()
  if (!value) return t('account.profile.nicknameRequired')
  if (length(value) > NICKNAME_MAX) return t('account.profile.nicknameTooLong', { max: NICKNAME_MAX })
  if (!READABLE.test(value)) return t('account.profile.nicknameUnreadable')
  return ''
})
const introError = computed(() =>
  length(intro.value) > INTRO_MAX ? t('account.profile.introTooLong', { max: INTRO_MAX }) : ''
)

const dirty = computed(() => nickname.value !== props.savedNickname || intro.value !== props.savedIntro)
const valid = computed(() => !nicknameError.value && !introError.value)

function revert() {
  nickname.value = props.savedNickname
  intro.value = props.savedIntro
}

function submit() {
  if (!dirty.value || !valid.value) return
  // The nickname goes to the server without its edge spaces, and the field
  // shows what was sent — so once the record takes it, the draft matches it.
  nickname.value = nickname.value.trim()
  emit('save', { nickname: nickname.value, intro: intro.value })
}

// ---- Avatar: the file box lives here, the upload does not ----

const avatarInput = ref<HTMLInputElement | null>(null)

function pick() {
  avatarInput.value?.click()
}

function onAvatarPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) emit('avatar-picked', file)
}
</script>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
/* 这一页不再自己设宽度：宽度和水平内距由浮层的内容列给（SettingsOverlay 的
   `.so__content`，720 居中）。以前这里写死 `--page-w-read`（660），比别的设置页窄
   一截，同一条内容列里只有它不一样。 */

.profile__head {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  justify-content: space-between;
}

.profile__home {
  display: inline-flex;
  flex-shrink: 0;
  gap: 4px;
  align-items: center;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
  text-decoration: none;
  transition: color var(--dur-quick) var(--ease-standard);
}

.profile__home:hover {
  color: var(--ink);
}

/* A row that holds a field: the label sits on the field's first line. */
.srow--field {
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
  padding: 20px 24px;
}

.srow--field > .srow__k {
  padding-top: 10px;
}

.srow__stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding-top: 10px;
}

.handle {
  font-family: var(--font-mono);
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  overflow-wrap: anywhere;
}

.field-note {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.avatar-field {
  display: flex;
  gap: 16px;
  align-items: center;
}

.avatar-field__img {
  flex-shrink: 0;
  font-size: 23px;
  font-weight: 600;
}

.avatar-field__side {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.avatar-field__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.avatar-field__input {
  display: none;
}

/* 断点对齐共享 token（`styles/breakpoints.scss`）：599.98 → 767.98，和这一页
   一起加载的 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .profile__head {
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }

  .srow--field {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
    padding: 16px;
  }

  .srow--field > .srow__k,
  .srow__stack {
    padding-top: 0;
  }
}
</style>
