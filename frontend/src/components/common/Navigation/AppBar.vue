<template>
  <!-- `background` (not a fixed grey): the bar frames every page, so a Material palette
       name like grey-lighten-5 would pin it to #FAFAFA in dark theme while the text inside
       follows --v-theme-on-surface and turns unreadable. -->
  <v-system-bar
    window
    color="background"
    absolute
    :height="40"
    class="app-system-bar"
    :class="{ 'app-system-bar--window-buttons': titleBarInset }"
    :data-tauri-drag-region="titleBarInset ? 'deep' : undefined"
  >
    <div class="app-bar__start">
      <!-- The one switch for the sidebar: folds the pinned one on wide windows, opens the
           overlay one on tablets. Pages without a sidebar keep its room empty, so nothing
           after it moves from page to page. -->
      <BaseButton
        v-if="sidebar"
        data-sidebar-toggle
        kind="ghost"
        icon="mdi-page-layout-sidebar-left"
        size="sm"
        :aria-expanded="sidebar.shown ? 'true' : 'false'"
        :aria-controls="SIDEBAR_DRAWER_ID"
        :aria-label="sidebar.shown ? t('navigation.sidebar.collapse') : t('navigation.sidebar.expand')"
        :title="sidebar.shown ? t('navigation.sidebar.collapse') : t('navigation.sidebar.expand')"
        @click="sidebar.toggle()"
      />
      <span v-else class="app-bar__toggle-room" aria-hidden="true" />
      <!-- Back: in-app history first, the declared parent when there is none. A fixed slot,
           so 首页 and the place name never shift; where there is nowhere to go back to the
           slot stays empty (ParentBackButton never draws a dead arrow). -->
      <span class="app-bar__back">
        <ParentBackButton />
      </span>
      <router-link
        v-if="homeItem"
        :to="homeItem.to ?? '/inbox'"
        class="app-bar__home"
        :aria-label="homeLabel"
        :aria-current="onHome ? 'page' : undefined"
        data-testid="bar-home"
        @mouseenter="warmHome"
        @mouseleave="cancelPrefetch()"
        @focus="warmHome"
        @blur="cancelPrefetch()"
      >
        <CheeseLogo width="16" height="16" class="app-bar__mark" data-home-slot />
        <span>{{ homeItem.title }}</span>
        <span v-if="homeItem.badge" class="side-nav__count" aria-hidden="true">{{ countLabel(homeItem.badge) }}</span>
        <TopicRailBadge v-else-if="homeItem.dot" dot aria-hidden="true" />
      </router-link>
      <ProjectSwitcher />
    </div>

    <div class="app-bar__middle">
      <button
        type="button"
        class="app-bar__search"
        data-testid="bar-search"
        :title="t('global.labelWithAside', { label: t('navigation.palette.open'), aside: searchKey })"
        :aria-label="t('navigation.palette.open')"
        aria-haspopup="dialog"
        @click="openPalette()"
      >
        <v-icon size="16" icon="mdi-magnify" />
        <span class="app-bar__search-label">{{ t('navigation.palette.open') }}</span>
        <kbd class="app-bar__kbd">{{ searchKey }}</kbd>
      </button>
      <AgentRailEntry class="app-bar__agent" />
    </div>

    <div class="app-bar__end">
      <slot name="end" />
      <!-- The desktop app's downloaded update, waiting for a restart; nothing restarts until clicked. -->
      <DesktopUpdateReady />
      <HelpAndFeedbackMenu :logged-in="loggedIn" />
      <!-- Signed in, the language is in the account menu; signed-out visitors have no such menu. -->
      <LanguageToggle v-if="!loggedIn" />
      <AccountButton />
    </div>
  </v-system-bar>
</template>

<script setup lang="ts">
// 桌面的第一层：一条 40px 的顶栏（docs/design-system.md §1.4）。左边侧栏开关、返回、「首页」（带
// 等你处理的总件数）和你所在的去处；中间搜索和问芝士；右边帮助与反馈、头像。上面的东西不随
// 换页、收起侧栏挪位置。桌面版 macOS 上它就是窗口的标题栏：整条可拖，左边让出三颗窗口按钮。
//
// 登录态和导航数据由外壳交进来（`layouts/AppShell.vue` 的 provideAppNav）：这个组件在
// `src/components/**` 下，不能自己去读账号服务或路由（`.claude/rules/architecture.md`）。
import { computed } from 'vue'

import { useNavigation } from '@/composables/useNavigation'
import { SIDEBAR_DRAWER_ID } from '@/composables/useSidebarCollapse'

import AccountButton from './AccountButton.vue'
import { homeItemOf, useAppNav } from './appNav'
import DesktopUpdateReady from './DesktopUpdateReady.vue'
import HelpAndFeedbackMenu from './HelpAndFeedbackMenu.vue'
import ParentBackButton from './ParentBackButton.vue'
import ProjectSwitcher from './ProjectSwitcher.vue'

import CheeseLogo from '@/assets/logo-plain.svg?component'
import { openPalette } from '@/commands/palette/state'
import AgentRailEntry from '@/components/assistant/AgentRailEntry.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import { t } from '@/i18n'
import { titleBarOverlay } from '@/lib/desktopApp'
import { cancelPrefetch, prefetchOnHover } from '@/lib/routePrefetch'
import { countLabel } from '@/lib/topicTree'

const props = defineProps<{ loggedIn: boolean }>()

const appNav = useAppNav()
const navigation = useNavigation()
const sidebar = computed(() => appNav?.sidebar.value ?? null)
const homeItem = computed(() => homeItemOf(appNav?.items.value ?? []))
const onHome = computed(() => !!homeItem.value?.match?.(navigation?.route?.path ?? ''))
// 角标和小点是画给眼睛的，读屏从名字里听到件数或「有新动态」。
const homeLabel = computed(() => {
  const item = homeItem.value
  if (!item) return undefined
  if (item.badge) return t('global.labelWithAside', { label: item.title, aside: item.badge })
  if (item.dot) return t('global.labelWithAside', { label: item.title, aside: t('home.nav.unreadActivity') })
  return item.title
})

function warmHome() {
  const to = homeItem.value?.to
  if (to) prefetchOnHover({ router: navigation?.router, to })
}

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
const searchKey = isMac ? '⌘K' : 'Ctrl K'

const titleBarInset = titleBarOverlay()
const loggedIn = computed(() => props.loggedIn)
</script>

<style>
/* VSystemBar dims the whole bar with `opacity: var(--v-medium-emphasis-opacity)`, which
   fades its own background too and lands small text under 4.5:1. Restore it and take the
   token that means secondary text. The bottom line is the frame's one horizontal line
   (§1.4): the content and the sidebar below draw none of their own. */
.app-system-bar.app-system-bar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 12px;
  padding: 0 8px;
  color: rgb(var(--v-theme-on-surface-variant));
  border-bottom: 1px solid var(--line);
  opacity: 1;
}

/* In the desktop app on macOS the window buttons sit at this bar's left end
   (desktop/src-tauri/src/main.rs places them, centred in its 40px); the bar's own content,
   the sidebar toggle and the back button first, starts past them. Buttons and links are
   controls, not drag area. The width lives in style.css (`--window-buttons-inset`), 0 elsewhere. */
.app-system-bar.app-system-bar--window-buttons {
  padding-inline-start: max(8px, var(--window-buttons-inset));
}

.app-bar__start,
.app-bar__middle,
.app-bar__end {
  display: flex;
  gap: 4px;
  align-items: center;
  min-width: 0;
}

.app-bar__end {
  justify-content: flex-end;
}

.app-bar__middle {
  gap: 8px;
}

.app-bar__toggle-room,
.app-bar__back {
  flex: none;
  width: 28px;
}

.app-bar__back {
  display: inline-flex;
  align-items: center;
}

/* 顶栏里能点的这一簇是同一种形状：高 28、无边、悬停出底色。 */
.app-bar__home {
  display: inline-flex;
  flex: none;
  gap: 6px;
  align-items: center;
  height: 28px;
  padding: 0 8px;
  font-size: 13px;
  color: var(--text);
  text-decoration: none;
  border-radius: var(--radius-sm);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.app-bar__home:hover {
  background: var(--fill-2);
}

.app-bar__home[aria-current='page'] {
  font-weight: 500;
  color: var(--ink);
}

.app-bar__mark {
  flex: none;
  fill: var(--muted);
}

.app-bar__home[aria-current='page'] .app-bar__mark {
  fill: var(--ink);
}

/* 两个类名：压过 style.css 里给裸 <button> 清掉边框和底色的那一条（0,1,1）。 */
.app-system-bar .app-bar__search {
  display: flex;
  gap: 8px;
  align-items: center;
  width: clamp(200px, 28vw, 360px);
  height: 28px;
  padding: 0 8px 0 10px;
  font: inherit;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  transition: border-color var(--dur-quick) var(--ease-standard);
}

.app-system-bar .app-bar__search:hover {
  color: var(--text);
  border-color: var(--faint);
}

.app-bar__search-label {
  flex: 1;
  text-align: left;
}

.app-bar__kbd {
  padding: 0 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 16px;
  color: var(--muted);
  border: 1px solid var(--line-2);
  border-radius: 4px;
}

.app-system-bar .app-bar__agent.agent-rail {
  width: 28px;
  height: 28px;
  margin: 0;
}

/* 语言开关自己的样式是给页头用的（白底、描边、高 40），在顶栏里压成和旁边的按钮一样。
   选择器写两遍类名是为了压过它自己的 scoped 规则。 */
.app-system-bar.app-system-bar .language-toggle {
  min-height: 28px;
  height: 28px;
  padding: 0 8px;
  font-size: 13px;
  color: var(--muted);
  background: transparent;
  border: 0;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.app-system-bar.app-system-bar .language-toggle:hover {
  background: var(--fill);
}
</style>
