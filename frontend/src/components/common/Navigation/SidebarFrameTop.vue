<template>
  <!-- 侧栏最上面两样，四处侧栏（项目、首页、空间、后台）都一样：侧栏头写你在哪儿（点开换项目），
       下面一整条搜索。回首页没有专门一行（2026-11-19 蔡松洋定：四处一起删）：项目切换器
       那一份菜单里有「首页」一行，命令面板也搜得到「待办」。 -->
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
</template>

<script setup lang="ts">
import ProjectSwitcher from './ProjectSwitcher.vue'

import { openPalette } from '@/commands/palette/state'
import { t } from '@/i18n'

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
</style>
