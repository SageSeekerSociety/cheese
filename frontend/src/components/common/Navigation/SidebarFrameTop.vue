<template>
  <!-- 侧栏最上面三样，四处侧栏（项目、首页、空间、后台）都一样：侧栏头写你在哪儿（点开换项目），
       下面一整条搜索，再下面「首页」一行，带等你处理的总件数——从哪儿都由这一行回首页。 -->
  <ProjectSwitcher />
  <div class="frame-search-row">
    <button
      type="button"
      class="frame-search"
      data-testid="sidebar-search"
      :title="t('global.labelWithAside', { label: t('navigation.palette.open'), aside: searchKey })"
      :aria-label="t('navigation.palette.open')"
      aria-haspopup="dialog"
      @click="openPalette()"
    >
      <v-icon size="16" icon="mdi-magnify" />
      <span class="frame-search__label">{{ t('navigation.palette.open') }}</span>
      <kbd class="frame-search__kbd">{{ searchKey }}</kbd>
    </button>
  </div>
  <div v-if="homeItem" class="frame-home-row">
    <router-link
      :to="homeItem.to ?? '/inbox'"
      class="frame-home"
      :class="{ 'frame-home--current': atHome }"
      :aria-current="atHome ? 'page' : undefined"
      :aria-label="homeLabel"
      data-testid="sidebar-home"
      @mouseenter="warmHome"
      @mouseleave="cancelPrefetch()"
      @focus="warmHome"
      @blur="cancelPrefetch()"
    >
      <CheeseLogo width="16" height="16" class="frame-home__mark" data-home-slot />
      <span class="frame-home__label">{{ homeItem.title }}</span>
      <span v-if="homeItem.badge" class="side-nav__count" aria-hidden="true">{{ countLabel(homeItem.badge) }}</span>
      <TopicRailBadge v-else-if="homeItem.dot" dot aria-hidden="true" />
    </router-link>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

import { homeItemOf, useAppNav } from './appNav'
import ProjectSwitcher from './ProjectSwitcher.vue'

import CheeseLogo from '@/assets/logo-plain.svg?component'
import { openPalette } from '@/commands/palette/state'
import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import { t } from '@/i18n'
import { cancelPrefetch, prefetchOnHover } from '@/lib/routePrefetch'
import { countLabel } from '@/lib/topicTree'

const nav = useAppNav()
const navigation = useNavigation()

const homeItem = computed(() => homeItemOf(nav?.items.value ?? []))
// 「首页」这一行只在它通往的那一页上算选中：团队、空间那几页也归首页一带（侧栏头写「首页」），
// 但侧栏上标出当前位置的是团队、空间自己那一行。
const atHome = computed(() => {
  const to = homeItem.value?.to
  return typeof to === 'string' && navigation?.route?.path === to
})
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
</script>

<style scoped>
.frame-search-row {
  flex: none;
  padding: 8px 8px 4px;
}

.frame-search {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  height: 32px;
  padding: 0 10px;
  font: inherit;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-quick) var(--ease-standard);
}

.frame-search:hover {
  color: var(--text);
  border-color: var(--faint);
}

.frame-search__label {
  flex: 1;
  text-align: left;
}

.frame-search__kbd {
  padding: 0 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 16px;
  color: var(--muted);
  border: 1px solid var(--line-2);
  border-radius: 4px;
}

/* 这一行和下面各侧栏的行同一个样子（TopicRailPinnedRows 的 .nav-row）：32px 高，图标在
   16px 那一列，选中是 --line-2 的底、字加粗。 */
.frame-home-row {
  flex: none;
  padding: 4px 8px 0;
}

.frame-home {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 32px;
  padding: 0 12px 0 8px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  text-decoration: none;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.frame-home:hover {
  background: var(--fill-2);
}

.frame-home--current,
.frame-home--current:hover {
  font-weight: 600;
  color: var(--ink);
  background: var(--line-2);
}

.frame-home__mark {
  flex: none;
  fill: var(--faint);
}

.frame-home__label {
  flex: 1;
  min-width: 0;
}

@media (hover: none) {
  .frame-home {
    min-height: 44px;
  }
}
</style>
