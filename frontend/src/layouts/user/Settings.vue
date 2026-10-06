<template>
  <SettingsOverlay
    :label="t('account.settings.title')"
    :groups="groups"
    :active="active"
    :index-to="{ name: 'UserSettings' }"
    :close-label="t('account.settings.close')"
    :close-title="t('account.settings.closeHint')"
    :back-label="t('account.settings.back')"
    @close="close"
  >
    <template #head>
      <div class="me">
        <UserAvatar :avatar="avatar" :name="user?.nickname || user?.username" size="32" />
        <div class="me__text">
          <span class="me__name">{{ user?.nickname || user?.username }}</span>
          <span v-if="user" class="me__handle">{{ user.username }}</span>
        </div>
      </div>
    </template>
    <router-view />
  </SettingsOverlay>
</template>

<script lang="ts" setup>
// 个人设置：一层盖在整个窗口上的浮层（components/common/SettingsOverlay）。
// 关掉回到打开之前的那一页；直接从链接打开、之前没有页面时回首页。
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { getAvatarUrl } from '@/utils/materials'

import SettingsOverlay from '@/components/common/SettingsOverlay.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { closeOverlay } from '@/lib/backOut'
import { pageBeforeSettings } from '@/lib/settingsReturn'
import AccountService from '@/services/account'
import { settingsGroups } from '@/views/user/settings/sections'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()

const user = AccountService._user
const avatar = computed(() => (user.value?.avatarId ? getAvatarUrl(user.value.avatarId) : undefined))

const groups = computed(() => settingsGroups())

/** 停在「个人设置」这一条自己的地址上时没有当前项：手机上那就是目录。 */
const active = computed(() => (route.name === 'UserSettings' ? null : String(route.name)))

// 桌面上没有单独的目录页，目录一直在左边：落到第一项。
watch(
  [() => route.name, mdAndUp],
  ([name, desktop]) => {
    if (name === 'UserSettings' && desktop) router.replace({ name: 'UserSettingsProfile' })
  },
  { immediate: true }
)

function close() {
  closeOverlay(router, pageBeforeSettings({ name: 'HomeHub' }))
}
</script>

<style scoped>
.me {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 0 10px 4px;
}

.me__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.me__name {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.me__handle {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
</style>
