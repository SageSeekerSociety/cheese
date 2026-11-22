<template>
  <!-- `background` (not a fixed grey): the title bar frames every page, so a
       Material palette name like grey-lighten-5 would pin it to #FAFAFA in dark
       theme while the text inside follows --v-theme-on-surface → unreadable. -->
  <v-system-bar
    window
    color="background"
    absolute
    class="app-system-bar"
    :class="{ 'app-system-bar--window-buttons': titleBarInset }"
    :data-tauri-drag-region="titleBarInset ? 'deep' : undefined"
  >
    <!-- 平板横放那一档（960–1180）：侧栏默认收起，这颗开关管它（原来在左边那一列顶上）。 -->
    <BaseButton
      v-if="compact && hasSidebar"
      data-sidebar-toggle
      icon="mdi-page-layout-sidebar-left"
      size="sm"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-controls="SIDEBAR_DRAWER_ID"
      :aria-label="open ? t('navigation.sidebar.collapse') : t('navigation.sidebar.expand')"
      :title="open ? t('navigation.sidebar.collapse') : t('navigation.sidebar.expand')"
      @click="toggle"
    />
    <!-- 侧栏上「首页」那一行（回首页，带等你处理的件数）：侧栏看不见的时候（这一页没有侧栏、
         平板上侧栏收着）画在这里，回首页总够得着（Navigation/appNav.ts）。 -->
    <router-link
      v-if="frameInBar && homeItem"
      :to="homeItem.to ?? '/inbox'"
      class="app-bar-home"
      :aria-label="homeLabel"
      data-testid="bar-home"
    >
      <CheeseLogo width="16" height="16" class="app-bar-home__mark" data-home-slot />
      <span>{{ homeItem.title }}</span>
      <span v-if="homeItem.badge" class="side-nav__count" aria-hidden="true">{{ countLabel(homeItem.badge) }}</span>
      <TopicRailBadge v-else-if="homeItem.dot" dot aria-hidden="true" />
    </router-link>
    <ParentBackButton />
    <div class="text-caption font-weight-bold title-bar flex-grow-1">
      <span class="text-caption">{{ currentTitle }}</span>
    </div>
    <!-- 帮助与反馈。**它现在是一个菜单**（`HelpAndFeedbackMenu`），桌面和手机共用：
         底下的「我的反馈」和「管理后台」今天只能二级跳，收进菜单之后三个目的地都是一次
         可达；入口本身也从「反馈」变成「帮助与反馈」—— 需求方原话是那两个字太不显眼。
         代价是直达反馈中心多一次点击，取舍写在那个组件的文件头里。
         登录与否都显示：没登录的人遇到的问题同样值得记下来（未读点那时画不出来，
         因为计数要登录）。 -->
    <!-- 桌面 app 的新版本下载好了、等一次重启时才出现；不点就不重启。 -->
    <DesktopUpdateReady />
    <HelpAndFeedbackMenu :logged-in="loggedIn" />
    <!-- 登录后语言在「我」的菜单里（和外观并排）；没登录的人没有那个菜单，语言留在这儿。 -->
    <LanguageToggle v-if="!loggedIn" />
    <!-- 问芝士和我的头像：平时在侧栏底，侧栏看不见时在这里。 -->
    <template v-if="frameInBar">
      <AgentRailEntry class="app-bar-agent" />
      <AccountButton placement="bar" />
    </template>
  </v-system-bar>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { SIDEBAR_FOLD_KEY } from '@/composables/sidebarWidth'
import { usePageTitle } from '@/composables/usePageTitle'
import { SIDEBAR_DRAWER_ID, useSidebarCollapse } from '@/composables/useSidebarCollapse'

import AccountButton from './AccountButton.vue'
import { homeItemOf, useAppNav } from './appNav'
import DesktopUpdateReady from './DesktopUpdateReady.vue'
import HelpAndFeedbackMenu from './HelpAndFeedbackMenu.vue'
import ParentBackButton from './ParentBackButton.vue'

import CheeseLogo from '@/assets/logo-plain.svg?component'
import AgentRailEntry from '@/components/assistant/AgentRailEntry.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import { rememberedFold } from '@/components/layout-area'
import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import { t } from '@/i18n'
import { titleBarOverlay } from '@/lib/desktopApp'
import { countLabel } from '@/lib/topicTree'
import { usePageTitleStore } from '@/stores/title'

// 登录态由外壳传进来（`layouts/AppShell.vue` 本来就有它）：这个组件在
// `src/components/**` 下，不能自己去读 `services/account`
// （`.claude/rules/architecture.md`）。
const props = withDefaults(defineProps<{ loggedIn: boolean; hasSidebar?: boolean }>(), { hasSidebar: false })

const { compact, open, toggle } = useSidebarCollapse()
const appNav = useAppNav()
const homeItem = computed(() => homeItemOf(appNav?.items.value ?? []))
const homeLabel = computed(() => {
  const item = homeItem.value
  if (!item) return undefined
  if (item.badge) return t('global.labelWithAside', { label: item.title, aside: item.badge })
  if (item.dot) return t('global.labelWithAside', { label: item.title, aside: t('home.nav.unreadActivity') })
  return item.title
})
// 侧栏看不见：这一页没有侧栏，平板上侧栏收着，或宽档里侧栏收成了一条。这时首页、问芝士、
// 头像临时住在顶栏。
const folded = rememberedFold(SIDEBAR_FOLD_KEY)
const frameInBar = computed(() => !props.hasSidebar || (compact.value ? !open.value : folded.value))

const { updateTrigger } = usePageTitleStore()
const { getRouteHierarchy } = usePageTitle()

const titleBarInset = titleBarOverlay()
const currentTitle = ref(t('global.cheese'))

const updateTitle = () => {
  const hierarchy = getRouteHierarchy.value
  for (const item of hierarchy) {
    if (item.meta.isFullPage) {
      currentTitle.value = item.title
      return
    }
  }
  currentTitle.value = t('global.cheese')
}

watch([getRouteHierarchy, () => updateTrigger], updateTitle, { immediate: true })

const loggedIn = computed(() => props.loggedIn)
</script>

<style>
.title-bar {
  height: 100%;
  user-select: none;
  display: flex;
  justify-content: center;
  align-items: center;
}

/* VSystemBar dims the WHOLE bar with `opacity: var(--v-medium-emphasis-opacity)`,
   which lands the 12px title at 0.62 ink over the canvas — #7f8184 on #f7f8fa,
   3.68:1, under the 4.5:1 AA needs for small text. (Pre-existing: it measured
   3.70:1 on the old grey-lighten-5 too.) Opacity is the wrong tool anyway — it
   fades the bar's own background as well. Restore it and reach for the token
   that MEANS "secondary text", `on-surface-variant` (= --muted): 4.81:1 light,
   7.11:1 dark, and it is a real colour rather than a fade. */
.app-system-bar.app-system-bar {
  opacity: 1;
  color: rgb(var(--v-theme-on-surface-variant));
}

/* In the desktop app on macOS the window buttons sit at this bar's left end
   (desktop/src-tauri/src/main.rs places them); the bar's own content starts a
   little past them. Starting exactly at 64px put the back arrow against the
   green button, where it read as a fourth window button. The width lives in
   style.css (`--window-buttons-inset`), 0 off that platform; every other surface
   that clears the buttons reads the same number. */
.app-system-bar.app-system-bar--window-buttons {
  padding-inline-start: var(--window-buttons-inset);
}

/* 右上这一簇是同一种形状：高 28、无边、悬停出底色。语言开关自己的样式是给页头
   用的（白底、描边、高 40），在这条系统栏里压成和旁边的按钮一样。高度**钉死**，
   不靠行盒 + 内边距去撑 —— 撑出来的高度随字体变，同一簇里就会差出一两像素。
   选择器写两遍类名是为了压过语言开关自己的 scoped 规则（同特异度时看注入顺序）。 */
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

/* 顶栏里的「首页」：和这一簇按钮一样高 28、无边、悬停出底色。 */
.app-system-bar .app-bar-home {
  display: inline-flex;
  flex: none;
  gap: 6px;
  align-items: center;
  height: 28px;
  padding: 0 8px;
  margin-inline-end: 4px;
  font-size: 13px;
  color: var(--text);
  text-decoration: none;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.app-system-bar .app-bar-home:hover {
  background: var(--fill);
}

.app-system-bar .app-bar-home__mark {
  fill: var(--muted);
}

.app-system-bar .app-bar-agent.agent-rail {
  width: 32px;
  height: 32px;
  margin: 0 2px;
}

.cursor-pointer {
  cursor: pointer;
}
</style>
