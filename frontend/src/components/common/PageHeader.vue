<template>
  <div
    v-if="renderHeader()"
    v-scroll:#app-scrollable="onScroll"
    class="page-header text-high-emphasis"
    :class="{ 'page-header-mobile': $vuetify.display.mobile && !showOnMobile }"
    :style="{ '--app-page-header-bg-opacity': props.enableScrollEffect ? bgProgress : props.maxOpacity }"
  >
    <template v-if="$slots.default">
      <slot></slot>
    </template>

    <template v-else>
      <!-- 图标处理：可能是icon或图片 -->
      <template v-if="$vuetify.display.mdAndUp || showOnMobile">
        <template v-if="displayIcon">
          <v-icon v-if="displayIcon.type === 'icon'" size="24">{{ displayIcon.value }}</v-icon>
          <v-img v-else-if="displayIcon.type === 'image'" :src="displayIcon.value" width="24" height="24" />
        </template>
        <span v-if="title" class="text-subtitle-1">{{ title }}</span>
        <template v-else>
          <template v-for="(item, index) in displayItems" :key="index">
            <router-link v-if="item.isClickable" :to="item.path" class="text-subtitle-1 breadcrumb-link">
              {{ item.title }}
            </router-link>
            <span v-else class="text-subtitle-1">{{ item.title }}</span>
            <v-icon v-if="index < displayItems.length - 1" size="16">mdi-chevron-right</v-icon>
          </template>
        </template>
      </template>
    </template>

    <template v-if="$slots.tabs">
      <div></div>
      <slot name="tabs"></slot>
    </template>
    <template v-else-if="tabsComponent">
      <div></div>
      <component :is="tabsComponent" />
    </template>
    <v-spacer></v-spacer>

    <div v-if="hasActions && ($vuetify.display.mdAndUp || showOnMobile)" class="header-actions">
      <v-defaults-provider :defaults="{ VBtn: { color: 'on-surface', size: 'small', variant: 'text' } }">
        <component :is="actionsComponent" v-if="actionsComponent" />
        <slot name="actions"></slot>
      </v-defaults-provider>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { RouteIcon } from '@/types/title'

import { computed, ref, useSlots } from 'vue'
import { useDisplay } from 'vuetify'
import { clamp } from 'lodash-es'
import { storeToRefs } from 'pinia'

import { useBreadcrumb } from '@/composables/useBreadcrumb'

import { useNavigationStore } from '@/stores/navigation'

interface Props {
  // 自定义图标，不指定则使用面包屑的图标
  icon?: string
  title?: string
  // 指定面包屑的截取起始页面名称
  startFromRoute?: string
  // 是否启用滚动背景效果
  enableScrollEffect?: boolean
  // 背景透明度（滚动时达到的最大透明度）
  maxOpacity?: number
  // 移动端也把它当成完整页头：照常渲染、操作区照常显示、内边距不清零。
  //
  // 默认 false —— 页头默认只在 mdAndUp 出现，因为移动端顶部已经有 MobileAppBar
  // 顶着标题，再来一条面包屑只是重复。但有些页面的页头里放着该页**唯一**的操作入口
  // （公告/讨论的“新建”、实名信息的“显示明文”眼睛），这些页面把它关掉等于在手机上
  // 砍掉功能，所以由页面自己声明。
  showOnMobile?: boolean
}

// 获取插槽
defineSlots<{
  default?: () => any
  actions?: () => any
  tabs?: () => any
}>()

const slots = useSlots()

const props = withDefaults(defineProps<Props>(), {
  enableScrollEffect: true,
  maxOpacity: 0.75,
  showOnMobile: false,
})
const display = useDisplay()

const bgProgress = ref(0)
const { breadcrumbItems } = useBreadcrumb()
const headerStore = useNavigationStore()
const { actionsComponent, tabsComponent } = storeToRefs(headerStore)

const renderHeader = () => display.mdAndUp.value || props.showOnMobile || !!tabsComponent.value || !!slots.tabs

// 计算显示的面包屑项目
const displayItems = computed(() => {
  if (props.startFromRoute) {
    const startIdx = breadcrumbItems.value.findIndex((item) => item.name === props.startFromRoute)
    if (startIdx !== -1) {
      return breadcrumbItems.value.slice(0, startIdx + 1).reverse()
    }
  }

  // 默认逻辑：找到最后一个 SpacesDetail 并从那里开始
  const lastIdx = breadcrumbItems.value.findLastIndex((item) => item.name === 'SpacesDetail')
  return breadcrumbItems.value.slice(0, lastIdx).reverse()
})

// 计算显示的图标
const displayIcon = computed((): RouteIcon | null => {
  if (props.icon) {
    return { type: 'icon', value: props.icon }
  }

  const firstItem = displayItems.value[0]
  if (firstItem?.icon) {
    return firstItem.icon
  }

  return null
})

// 是否有动作区域
const hasActions = computed(() => !!actionsComponent.value || !!slots.actions)

// 滚动效果
const onScroll = (e: Event) => {
  if (!props.enableScrollEffect) return

  const scrollTopPx = (e.target as HTMLElement).scrollTop
  bgProgress.value = clamp(scrollTopPx / 48, 0, props.maxOpacity)
}
</script>

<style lang="scss" scoped>
// 内容区页头。这里的每一条都必须写全 —— 以前 styles/common.scss 里有一个同名的
// 全局 .page-header，本组件靠 scoped 选择器特异性更高赢下了 padding 和
// backdrop-filter，却因为自己没写 height / justify-content，把全局那两条原样漏了
// 进来（实测：声明 padding: 12px 16px，标题实际落在 top 9.5px，因为高度被钉在
// 48px）。同名类现已消除（侧栏顶栏改叫 .sidebar-header），组件不再从任何地方继承。
.page-header {
  display: flex;
  flex-direction: row;
  align-items: center;
  // 页头高度是设计 token：TaskHeader / teams Explore 都按 --app-page-header-height
  // 做负偏移把自己顶到页头下面，所以这个值不能由内容撑，必须钉死。
  height: var(--app-page-header-height);
  justify-content: space-between;
  // 高度既然钉死，纵向 padding 就不起作用（内容在 48px 内居中），只留横向的。
  padding: 0 16px;
  gap: 8px;
  border-bottom: var(--app-page-header-rule);
  background: rgba(var(--v-theme-surface), var(--app-page-header-bg-opacity, 0));
  backdrop-filter: blur(8px);
  position: sticky;
  top: 0;
  z-index: 10;

  &.page-header-mobile {
    padding: 0;
  }
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.breadcrumb-link {
  text-decoration: none;
  color: inherit;
  transition: color 0.2s;

  &:hover {
    color: rgb(var(--v-theme-primary));
  }
}
</style>
