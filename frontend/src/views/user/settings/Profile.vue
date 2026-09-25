<template>
  <div class="settings-page profile">
    <header class="profile__head">
      <div>
        <h1 class="t-page-title">{{ t('account.profile.title') }}</h1>
        <p class="settings-page__lede">{{ t('account.profile.lede') }}</p>
      </div>
      <router-link v-if="user" class="profile__home" :to="{ name: 'UserDefault', params: { id: user.id } }">
        {{ t('account.profile.viewPage') }}
        <v-icon icon="mdi-chevron-right" size="16" />
      </router-link>
    </header>

    <!-- Save is the page's one main action, so it is the only amber on it
         (design-system §1.6); it exists only while there is something to save. -->
    <form v-if="user" class="settings-card" novalidate @submit.prevent="save">
      <div class="srow srow--field">
        <span class="srow__k">{{ t('account.profile.avatar') }}</span>
        <div class="avatar-field">
          <UserAvatar class="avatar-field__img" :avatar="shownAvatar" :name="avatarSeed" size="64" />
          <div class="avatar-field__side">
            <div class="avatar-field__actions">
              <v-btn
                variant="outlined"
                color="on-surface"
                :loading="changingAvatar"
                :disabled="removingAvatar"
                @click="avatarInput?.click()"
              >
                {{ t('account.profile.changeAvatar') }}
              </v-btn>
              <v-btn
                v-if="canRemoveAvatar"
                variant="text"
                color="on-surface"
                :loading="removingAvatar"
                :disabled="changingAvatar"
                @click="removeAvatar"
              >
                {{ t('account.profile.removeAvatar') }}
              </v-btn>
            </div>
            <span class="field-note">{{ t('account.profile.avatarHint') }}</span>
          </div>
          <input
            ref="avatarInput"
            class="avatar-field__input"
            type="file"
            :accept="AVATAR_TYPES.join(',')"
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
          <span class="handle">@{{ user.username }}</span>
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

      <Transition name="foot">
        <div v-if="dirty" class="foot-reveal">
          <div class="foot-reveal__clip">
            <div class="profile__foot">
              <span class="profile__foot-note">{{ t('account.profile.unsaved') }}</span>
              <v-btn variant="text" color="on-surface" :disabled="saving" @click="revert">
                {{ t('account.profile.revert') }}
              </v-btn>
              <v-btn type="submit" color="primary" variant="flat" :disabled="!valid" :loading="saving">
                {{ t('account.profile.save') }}
              </v-btn>
            </div>
          </div>
        </div>
      </Transition>
    </form>
  </div>
</template>

<script setup lang="ts">
import type { User } from '@/types/users'

import { computed, onMounted, ref, watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'

import { ensureDefaultAvatarId, globalDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'

import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { AvatarsApi } from '@/network/api/avatars'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import AccountService from '@/services/account'

// The server serves these four as images and anything else as an opaque file,
// which would show as a broken avatar. It sets no size limit of its own.
const AVATAR_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
const AVATAR_MAX_BYTES = 2 * 1024 * 1024
const NICKNAME_MAX = 50
const INTRO_MAX = 60
// A nickname needs at least one CJK character, letter or digit, so it cannot be
// only symbols or spaces.
const READABLE = /[0-9A-Za-z㐀-䶿一-鿿]/

const fail = (error: unknown, fallback: string) => toast.error(requestErrorMessage(error, fallback))

const user = computed(() => AccountService.user)

/**
 * Takes a change the server accepted into the signed-in person's own record at
 * once, then refetches it so the copy kept for the next visit has it too.
 */
function remember(change: Partial<User>) {
  if (AccountService.user) AccountService.user = { ...AccountService.user, ...change }
  void AccountService.updateUserInfo()
}

// ---- Nickname and intro: edited here, saved together ----

const savedNickname = computed(() => user.value?.nickname ?? '')
const savedIntro = computed(() => user.value?.intro ?? '')
const nickname = ref(savedNickname.value)
const intro = ref(savedIntro.value)
const saving = ref(false)

// The record can arrive or change after the page opens. A field follows it
// only while nobody has edited that field.
watch(savedNickname, (next, prev) => {
  if (nickname.value === prev) nickname.value = next
})
watch(savedIntro, (next, prev) => {
  if (intro.value === prev) intro.value = next
})

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

const dirty = computed(() => nickname.value !== savedNickname.value || intro.value !== savedIntro.value)
const valid = computed(() => !nicknameError.value && !introError.value)

function revert() {
  nickname.value = savedNickname.value
  intro.value = savedIntro.value
}

async function save() {
  if (!user.value || !dirty.value || !valid.value || saving.value) return
  saving.value = true
  const change = { nickname: nickname.value.trim(), intro: intro.value }
  try {
    await UserApi.updateUserInfo(user.value.id, change)
    remember(change)
    nickname.value = change.nickname
    toast.success(t('account.profile.saved'))
  } catch (error) {
    fail(error, t('account.profile.saveFailed'))
  } finally {
    saving.value = false
  }
}

// ---- Avatar: takes effect as soon as it is chosen ----

const avatarInput = ref<HTMLInputElement | null>(null)
const changingAvatar = ref(false)
const removingAvatar = ref(false)

// Same seed as the avatar in the navigation, so both show the same initial.
const avatarSeed = computed(() => user.value?.nickname || String(user.value?.id ?? ''))
const shownAvatar = computed(() => (isChosenAvatar(user.value?.avatarId) ? getAvatarUrl(user.value?.avatarId) : ''))
// Removing an avatar puts the platform default back, which shows as the
// initial; until that default is known there is nothing to put back.
const canRemoveAvatar = computed(() => globalDefaultAvatarId.value !== null && isChosenAvatar(user.value?.avatarId))

async function onAvatarPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !user.value) return
  if (!AVATAR_TYPES.includes(file.type)) {
    toast.error(t('account.profile.avatarWrongType'))
    return
  }
  if (file.size > AVATAR_MAX_BYTES) {
    toast.error(t('account.profile.avatarTooLarge'))
    return
  }
  changingAvatar.value = true
  try {
    const { data } = await AvatarsApi.createAvatar(file)
    await UserApi.updateUserInfo(user.value.id, { avatarId: data.avatarId })
    remember({ avatarId: data.avatarId })
  } catch (error) {
    fail(error, t('account.profile.avatarFailed'))
  } finally {
    changingAvatar.value = false
  }
}

async function removeAvatar() {
  const defaultId = globalDefaultAvatarId.value
  if (!user.value || defaultId === null) return
  removingAvatar.value = true
  try {
    await UserApi.updateUserInfo(user.value.id, { avatarId: defaultId })
    remember({ avatarId: defaultId })
  } catch (error) {
    fail(error, t('account.profile.removeAvatarFailed'))
  } finally {
    removingAvatar.value = false
  }
}

onMounted(ensureDefaultAvatarId)
</script>

<style scoped src="./settings-card.css"></style>

<style scoped>
.profile {
  max-width: var(--page-w-read);
}

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

/* The footer opens out of the card's bottom edge when there is something to
   save, and folds back into it when there is not. */
.foot-reveal {
  display: grid;
  grid-template-rows: 1fr;
}

.foot-reveal__clip {
  min-height: 0;
  overflow: hidden;
}

.foot-enter-active {
  transition:
    grid-template-rows var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}

.foot-leave-active {
  transition:
    grid-template-rows var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}

.foot-enter-from,
.foot-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
}

.profile__foot {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 16px 24px;
  background: var(--canvas);
  border-top: 1px solid var(--line);
}

.profile__foot-note {
  flex-grow: 1;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

@media (max-width: 599.98px) {
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

  .profile__foot {
    padding: 12px 16px;
  }
}
</style>
