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
    <HelpAndFeedbackMenu />
    <!-- 登录后语言在「我」的菜单里（和外观并排）；没登录的人没有那个菜单，语言留在这儿。 -->
    <LanguageToggle v-if="!loggedIn" />
  </v-system-bar>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { usePageTitle } from '@/composables/usePageTitle'

import HelpAndFeedbackMenu from './HelpAndFeedbackMenu.vue'
import ParentBackButton from './ParentBackButton.vue'

import LanguageToggle from '@/components/common/LanguageToggle.vue'
import { t } from '@/i18n'
import { titleBarOverlay } from '@/lib/desktopApp'
import AccountService from '@/services/account'
import { usePageTitleStore } from '@/stores/title'

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

const loggedIn = computed(() => AccountService._loggedIn.value)
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

/* In the desktop app on macOS the window buttons sit at this bar's left end,
   above the rail (desktop/src-tauri/src/main.rs places them); the bar's own
   content starts after the rail's width. */
.app-system-bar.app-system-bar--window-buttons {
  padding-inline-start: 64px;
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

.cursor-pointer {
  cursor: pointer;
}
</style>
