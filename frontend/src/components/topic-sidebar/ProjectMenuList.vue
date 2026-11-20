<script setup lang="ts">
// 项目菜单里的那一份清单：项目文档、项目名下那一行之外的几页、项目设置、转让或退出。
// 桌面上挂在总览、资料库下面的「更多」上（TopicRailPinnedRows），平板两栏里挂在项目名
// 上（TopicRailHeader）。两处是同一份，写在这里一次。
import TopicRailBadge from './TopicRailBadge.vue'

import { t } from '@/i18n'

defineProps<{
  menuPages: { key: string; label: string; icon: string }[]
  docsActive: boolean
  routeName: string | null
  terms: { project: string; topic: string }
  privateUnreadTotal: number
  canTransfer: boolean
  canLeave: boolean
}>()

const emit = defineEmits<{
  (e: 'open-page', key: string): void
  (e: 'select-docs'): void
  (e: 'open-transfer'): void
  (e: 'open-leave'): void
}>()
</script>

<template>
  <v-list density="compact" nav max-height="60vh">
    <v-list-item
      prepend-icon="mdi-file-document-outline"
      :title="t('navigation.project.docs')"
      :active="docsActive"
      @click="emit('select-docs')"
    />
    <v-list-item
      v-for="p in menuPages"
      :key="p.key"
      :prepend-icon="p.icon"
      :title="t(p.label, terms)"
      :active="routeName === p.key"
      @click="emit('open-page', p.key)"
    >
      <!-- 有人找你：私聊的未读挂在「成员」上。 -->
      <template v-if="p.key === 'project-members' && privateUnreadTotal > 0" #append>
        <TopicRailBadge :count="privateUnreadTotal" />
      </template>
    </v-list-item>
    <v-divider class="my-1" />
    <v-list-item
      prepend-icon="mdi-cog-outline"
      :title="t('work.projectSettings.title')"
      :active="routeName === 'project-settings'"
      @click="emit('open-page', 'project-settings')"
    />
    <!-- 只有转得动的人看得见：必然被拒的按钮比不给更糟。 -->
    <v-list-item
      v-if="canTransfer"
      prepend-icon="mdi-account-arrow-right-outline"
      :title="t('work.members.transfer')"
      @click="emit('open-transfer')"
    />
    <!-- 另一半：我不是所有者时换「退出项目」。所有者退不掉，只能先把项目交出去。 -->
    <v-list-item
      v-if="canLeave"
      prepend-icon="mdi-exit-to-app"
      :title="t('work.members.leave')"
      @click="emit('open-leave')"
    />
  </v-list>
</template>
