<template>
  <!-- 我的头像：点开用户菜单（设置、外观、退出登录……）。桌面上住在侧栏底，没有侧栏的
       页面和平板上侧栏收起时住在顶栏右边（appNav.ts）。没登录时是一颗「登录」。 -->
  <v-menu
    v-if="userMenu.loggedIn.value"
    v-model="userMenu.menuOpen.value"
    open-on-click
    :location="placement === 'bar' ? 'bottom end' : 'top start'"
    :offset="8"
    transition="scale-transition"
  >
    <template #activator="{ props }">
      <!-- button，不是 div：菜单里有设置和退出登录，只用键盘的人也得够得着。 -->
      <button
        type="button"
        class="account-button"
        :class="{ 'account-button--named': showName, 'account-button--open': userMenu.menuOpen.value }"
        data-testid="account-button"
        :aria-label="userMenu.nickname.value"
        v-bind="props"
        @pointerenter="rememberAvatar"
      >
        <!-- 组件写法而不是 v-tooltip 指令：指令没有开关，浮层开出去就收不回来。 -->
        <v-tooltip
          v-if="!showName"
          v-model="tipOpen"
          activator="parent"
          location="bottom"
          :text="userMenu.nickname.value"
          :disabled="userMenu.menuOpen.value"
        />
        <UserAvatar
          :avatar="userMenu.avatar.value ?? ''"
          :name="userMenu.nickname.value"
          :seed="userMenu.currentUser.value?.username"
          :size="28"
        />
        <span v-if="showName" class="account-button__name" data-user-content>{{ userMenu.nickname.value }}</span>
      </button>
    </template>
    <UserMenuCard :menu="userMenu" />
  </v-menu>
  <router-link v-else to="/account/signin" class="account-button account-button--signin">
    <v-icon icon="mdi-login" size="18" />
    <span>{{ t('account.signIn.submit') }}</span>
  </router-link>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useEventListener } from '@vueuse/core'

import { useUserMenu } from '@/composables/useUserMenu'

import UserMenuCard from './UserMenuCard.vue'

import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

withDefaults(
  defineProps<{
    /** 住在哪：侧栏底（带昵称，菜单往上弹）还是顶栏（只有头像，菜单往下弹）。 */
    placement?: 'sidebar' | 'bar'
    showName?: boolean
  }>(),
  { placement: 'sidebar', showName: false }
)

const userMenu = useUserMenu()

// v-tooltip 内部写死了 persistent，点别处、按 Esc 都关不掉它（#2455）：浮层开着时盯住
// 指针，指针到头像之外一动就收起。根元素从事件里记，`v-bind="props"` 带着 v-menu 自己的
// ref，模板 ref 会被顶掉。
let avatarEl: Element | null = null
function rememberAvatar(e: PointerEvent) {
  avatarEl = e.currentTarget as Element
}
const tipOpen = ref(false)
useEventListener(
  () => (tipOpen.value ? document : null),
  'pointermove',
  (e: PointerEvent) => {
    if (!avatarEl?.contains(e.target as Node)) tipOpen.value = false
  },
  { passive: true }
)
</script>

<style scoped>
.account-button {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 3px;
  color: var(--text);
  text-decoration: none;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.account-button--named {
  flex: 1;
  padding: 4px 6px;
}

.account-button:hover,
.account-button--open {
  background: var(--fill);
}

.account-button__name {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-button--signin {
  padding: 4px 10px;
  font-size: 13px;
  color: var(--muted);
}
</style>
