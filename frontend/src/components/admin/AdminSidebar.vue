<template>
  <SecondaryNavigation>
    <div class="sidebar-header">
      <span class="t-title">{{ t('admin.layout.brand') }}</span>
      <v-spacer></v-spacer>
    </div>

    <!-- 手机上侧栏是抽屉，每一行至少 44px 高；桌面上用紧凑行。不是管理员时只剩回去的那一条
         —— 分区要等服务端答了「你在名单里」才画。 -->
    <v-list
      nav
      :density="mdAndUp ? 'compact' : 'default'"
      :lines="false"
      class="side-nav pa-2"
      bg-color="transparent"
      :aria-label="t('admin.layout.sections')"
    >
      <template v-if="store.metaChecked && canEnter">
        <v-list-item
          v-for="section in visibleSections"
          :key="section.to"
          rounded="lg"
          :prepend-icon="section.icon"
          :to="section.to"
          :active="isCurrent(section.name)"
          :title="section.label()"
        >
          <template v-if="section.badge && unread > 0" #append>
            <span class="side-nav__count t-num" :aria-label="t('feedback.dashboard.kpi.unread')">{{ unread }}</span>
          </template>
        </v-list-item>
      </template>

      <!-- 回工作区，不是回反馈中心：`/` 那一格才是「工作区」的家（HomeDefault），
           `admin.layout.leave` 那句「返回工作区」说的是它。指到 `/feedback` 的话，
           话和去处对不上 —— 从后台回去会落在反馈中心，而不是刚才那个工作台。 -->
      <v-list-item rounded="lg" prepend-icon="mdi-arrow-left" to="/" :title="t('admin.layout.leave')" />
    </v-list>
  </SecondaryNavigation>
</template>

<script setup lang="ts">
// 管理后台的侧栏（`/admin/*` 的 `sidebar` 视图）。和首页、空间的侧栏同一套：侧栏头、
// `.side-nav` 的行、共用的宽度，手机上是抽屉。门（确认权限中 / 不是管理员）画在内容区
// （`AdminLayout`），这里只决定画哪几行。
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'

import { useAdminSections } from '@/composables/useAdminSections'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import { useFeedbackStore } from '@/stores/feedback'

const { t } = useI18n()
const { mdAndUp } = useDisplay()
const store = useFeedbackStore()
const { visibleSections, canEnter, isCurrent } = useAdminSections()

const unread = computed(() => store.counts.unread ?? 0)
</script>
