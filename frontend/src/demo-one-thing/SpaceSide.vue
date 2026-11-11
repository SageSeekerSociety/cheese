<script setup lang="ts">
// The course 空间's sidebar, row for row as the product's SpaceSidebar.vue
// draws it: ‹ back to 首页 with the space's name, 公告, 资料库, then 题目
// (全部题目 with how many were published since my last visit, and the space's
// 分类). 我的草稿 shows only to someone with drafts, and the 管理 group (待审核,
// 成员, 数据, 设置) only to the space's managers, so a student sees neither. A
// space has no channels.
//
// The product's component sits in a navigation drawer that needs the app's
// layout around it, so the demo draws the same rows itself, with the
// product's labels.
import { CATEGORIES } from './course'
import { SPACE } from './data'
import { openHome, openSpace, S } from './state'

import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

const onTasks = (cat: string | null) => S.center === 'space' && S.spacePage === 'tasks' && S.spaceCat === cat
</script>

<template>
  <nav class="ot-side ot-sp" data-zone="space-nav">
    <button type="button" class="sidebar-header ot-sp__head" :aria-label="t('spaces.sidebar.back')" @click="openHome()">
      <v-icon size="18" class="ot-sp__back">mdi-chevron-left</v-icon>
      <UserAvatar kind="org" :name="SPACE.name" :seed="String(SPACE.id)" size="20" />
      <span class="ot-sp__name t-title">{{ SPACE.name }}</span>
    </button>

    <v-list nav density="compact" :lines="false" class="side-nav pa-2" bg-color="transparent">
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-bullhorn-outline"
        :active="S.spacePage === 'announce'"
        :title="t('spaces.sidebar.announcements')"
        data-space-nav="announce"
        @click="openSpace('announce')"
      />
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-bookshelf"
        :active="S.spacePage === 'library'"
        :title="t('spaces.materials.title')"
        data-space-nav="library"
        @click="openSpace('library')"
      />

      <v-list-subheader>{{ t('spaces.sidebar.tasks') }}</v-list-subheader>
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-view-grid-outline"
        :active="onTasks(null)"
        :title="t('spaces.detail.allContests')"
        data-space-nav="tasks"
        @click="openSpace('tasks')"
      >
        <template v-if="S.spaceNew" #append>
          <span
            class="side-nav__count"
            :title="t('spaces.sidebar.newTasks', { n: S.spaceNew })"
            :aria-label="t('spaces.sidebar.newTasks', { n: S.spaceNew })"
            >{{ S.spaceNew }}</span
          >
        </template>
      </v-list-item>
      <v-list-item
        v-for="c in CATEGORIES"
        :key="c"
        rounded="lg"
        prepend-icon="mdi-shape-outline"
        :active="onTasks(c)"
        :title="c"
        :data-space-nav="`cat-${c}`"
        @click="openSpace('tasks', { spaceCat: c })"
      />
    </v-list>
  </nav>
</template>

<style scoped>
.ot-sp {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--canvas);
}

/* SpaceSidebar's header: a link back to 首页, the space's avatar and name. */
.ot-sp__head {
  position: static;
  justify-content: flex-start;
  width: 100%;
  font: inherit;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-block-end: 1px solid var(--line);
  backdrop-filter: none;
}

.ot-sp__head:hover {
  background: var(--fill-2);
}

.ot-sp__back {
  color: var(--muted);
}

.ot-sp__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
