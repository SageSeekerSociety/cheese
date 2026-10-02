<script setup lang="ts">
// 后台每一页的外形：`AppPage` 的 48px 页头和 `admin` 那一档宽度，和别的内部页面同一套；
// 再加上后台几页共有、页头那一行放不下的两样 —— 一句说明（`sub`）和筛选、页签
// （`#extra`），它们是正文的第一块。
//
// 页头右边的工具（`#tools`）在桌面上进页头。手机上页头不画（页名在顶栏里），工具就挪到
// 正文最上面一行：刷新、添加成员、统计窗口这些在手机上也要够得着。
import { useDisplay } from 'vuetify'

import AppPage from '@/components/common/AppPage.vue'

defineOptions({ name: 'AdminPage' })

defineProps<{
  title: string
  /** 一句话讲这一页管什么。省略就不画。 */
  sub?: string
}>()

defineSlots<{
  default?: () => unknown
  tools?: () => unknown
  extra?: () => unknown
}>()

const { mdAndUp } = useDisplay()
</script>

<template>
  <AppPage :title="title" width="admin">
    <template v-if="mdAndUp && $slots.tools" #controls>
      <slot name="tools" />
    </template>

    <div v-if="sub || $slots.extra || (!mdAndUp && $slots.tools)" class="admin-intro">
      <div v-if="!mdAndUp && $slots.tools" class="admin-intro__tools">
        <slot name="tools" />
      </div>
      <p v-if="sub" class="admin-intro__sub">{{ sub }}</p>
      <div v-if="$slots.extra" class="admin-intro__extra">
        <slot name="extra" />
      </div>
    </div>

    <slot />
  </AppPage>
</template>

<style scoped>
.admin-intro {
  flex: 0 0 auto;
  padding: 16px 24px 0;
}

.admin-intro__tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

/* 上限按 em 而不是 ch 算：`ch` 是「0」的宽度，约半个汉字，72ch 只装得下三十几个字。
   `text-wrap: pretty` 再兜一层，真要折行时不留孤字。 */
.admin-intro__sub {
  max-width: 60em;
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-wrap: pretty;
}

.admin-intro__sub + .admin-intro__extra {
  margin-top: 12px;
}

@media (max-width: 700px) {
  .admin-intro {
    padding-right: 16px;
    padding-left: 16px;
  }
}
</style>
