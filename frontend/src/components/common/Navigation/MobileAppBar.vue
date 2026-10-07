<template>
  <!-- `background`, not a fixed grey — see AppBar.vue for why. -->
  <v-app-bar color="background" :elevation="0" density="default" height="56" border="b-sm" app flat>
    <!-- 页面栈里的一层：← 回上一层。汉堡只留给**还真挂着抽屉**的那几页
         （1.0 的空间/小队详情，见 docs/plans/2026-08-18-mobile-shell-design.md §8）。 -->
    <template #prepend>
      <ParentBackButton />
      <v-app-bar-nav-icon v-if="hasDrawer" @click="toggleDrawer" />
    </template>

    <!-- 中间这一格：要么是路由的标题，要么由当前页自己填（它 Teleport 到这里）。
         手机上只有这一条顶栏，从不卸载——页面各画各的头的时候，两条头交替
         出现，Vuetify 会把 v-main 的 padding 从 57 滑到 0（.v-main 是
         transition: .2s），整页跟着抖一下。所以顶栏不动，动的是里面的内容。 -->
    <div v-if="barSlot" id="app-bar-slot" class="bar-slot" />
    <!-- 标题读页头那一号字 (.t-title 15/600)：屏幕上这条横条和页内页头是同一条，
         Vuetify 默认的 20px 会让它们看起来是两种东西。 -->
    <v-app-bar-title v-else class="t-title">
      {{ currentTitle }}
    </v-app-bar-title>

    <!-- 右侧动态操作按钮 -->
    <template #append>
      <!-- 渲染动态 actions 组件 -->
      <component :is="actionsComponent" v-if="actionsComponent" />

      <!-- 这一页页头上的命令（页面用 useCommands 登记的）：标了 primary 的一颗图标，
           其余进 ⋯。手机上页面不再为几颗按钮单占一行。 -->
      <BaseButton
        v-if="primaryAction"
        :icon="primaryAction.icon"
        kind="ghost"
        :to="primaryAction.to"
        :loading="primaryAction.loading"
        :disabled="primaryAction.disabled"
        :aria-label="primaryAction.label"
        :title="primaryAction.label"
        @click="primaryAction.onSelect?.()"
      />
      <AdaptiveMenu v-if="moreActions.length" :actions="moreActions">
        <template #activator="{ props: activator }">
          <BaseButton
            v-bind="activator"
            icon="mdi-dots-horizontal"
            kind="ghost"
            :aria-label="t('navigation.shell.more')"
            :title="t('navigation.shell.more')"
          />
        </template>
      </AdaptiveMenu>

      <!-- 通知的铃铛不在这儿了：手机上它的去处是底栏「待办」那一格
           (docs/plans/2026-08-18-mobile-shell-design.md §3.3)。 -->

      <!-- 帮助与反馈：**和桌面同一个组件**（`HelpAndFeedbackMenu`），这里只是换一种
           呈现（`compact` = 只留图标，手机上顶栏放不下那五个字）。和桌面同样只挂在一级
           目的地上 —— 页面栈里那几层右边是这一页自己的操作。
           它自己带高度与内边距：这一颗**过去没有任何样式规则**（桌上那颗有），所以
           它一直是 Vuetify 的默认尺寸，比旁边那颗语言开关高一档。 -->
      <HelpAndFeedbackMenu v-if="!backTo" compact />

      <!-- 用户头像菜单：只在一级目的地上。页面栈里的那几层（有 ← 的）右边留给
           这一页自己的操作——个人项在那儿既不相关，也挤掉了标题的宽度 (§3.4)。 -->
      <v-menu
        v-if="!backTo && userMenu.loggedIn.value"
        v-model="userMenu.menuOpen.value"
        open-on-click
        location="bottom start"
        :offset="8"
        transition="scale-transition"
      >
        <template #activator="{ props }">
          <!-- eslint-disable-next-line vue/no-restricted-syntax -- nav bar button whose look this component styles exactly (design-system §3.6 exception) -->
          <v-btn icon v-bind="props" variant="text">
            <!-- 头像走 UserAvatar（契约 §3.14）：挑过就画那张，没挑过 / 取不到就画按
                 handle 派生的彩色首字母，失败记忆归 utils/avatarFailures 一处管。以前
                 这里自己拼了一份 v-avatar + bar-avatar-char，和左栏 (LeftAppRail)、菜单卡
                 各画一份，同一个人在三处会画出三种样子；而且 useUserMenu 早已不再导出
                 avatarColor / avatarInitial，这里其实一直在读 undefined。 -->
            <UserAvatar
              :avatar="userMenu.avatar.value ?? ''"
              :name="userMenu.nickname.value"
              :seed="userMenu.currentUser.value?.username"
              :size="28"
            />
          </v-btn>
        </template>

        <UserMenuCard :menu="userMenu" />
      </v-menu>

      <!-- 未登录时的登录按钮 -->
      <BaseButton v-else-if="!backTo" kind="ghost" to="/account/signin" prepend-icon="mdi-account">{{
        t('account.signIn.submit')
      }}</BaseButton>
    </template>
  </v-app-bar>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'

import { useNavigation } from '@/composables/useNavigation'
import { usePageTitle } from '@/composables/usePageTitle'
import { useUserMenu } from '@/composables/useUserMenu'

import AdaptiveMenu from '../AdaptiveMenu.vue'

import HelpAndFeedbackMenu from './HelpAndFeedbackMenu.vue'
import ParentBackButton from './ParentBackButton.vue'
import UserMenuCard from './UserMenuCard.vue'

import { headerCommands, menuActionOf } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { useNavigationStore } from '@/stores/navigation'
import { usePageTitleStore } from '@/stores/title'

// 使用 composables
const userMenu = useUserMenu()

const navigation = useNavigation()
const navigationStore = useNavigationStore()

// 栈末端的路由自己说它回哪儿去（meta.backTo），而不是靠 history.back()——
// 从别处直接打开一个话题链接时，后退会离开这个 app。
const backTo = computed(() => {
  const meta = navigation?.route?.meta
  return typeof meta?.backTo === 'string' ? meta.backTo : null
})

// 汉堡由路由说了算：一个点了没反应的入口比没有入口更糟，而这条顶栏看不见自己
// 下面挂没挂侧栏——手机上没有侧栏的页面（/inbox、首页那两页）以前照样画一个汉堡。
const hasDrawer = computed(() => navigation?.route?.meta?.drawer === true)

// 这一页自己往顶栏里填内容（话题页的标题+阶段、话题列表的项目名、首页那对分段），
// 于是这里不写标题。谁填由路由声明，不靠去数 slot 里有没有东西——那样第一帧
// 永远是空的。
const barSlot = computed(() => navigation?.route?.meta?.barSlot === true)
const { updateTrigger } = usePageTitleStore()
const { getRouteHierarchy } = usePageTitle()
const { actionsComponent } = storeToRefs(navigationStore)

// 顶栏右边只放得下一颗主操作；标了几个 primary 就只取第一个，其余和没标的一起进 ⋯。
const topBarActions = computed(() =>
  headerCommands.value.map((command) => ({ ...menuActionOf(command), primary: !!command.header.primary }))
)
const primaryAction = computed(() => topBarActions.value.find((action) => action.primary) ?? null)
const moreActions = computed(() => topBarActions.value.filter((action) => action !== primaryAction.value))

const currentTitle = ref(t('global.cheese'))

// 切换抽屉状态
const toggleDrawer = () => {
  navigationStore.toggleSecondaryDrawer()
}

// 顶栏写的是**当前页**的标题，也就是路由层级里最深的那一个。
//
// 它以前取的是第一个 isFullPage 的**祖先**，而工作台那条路由上写着
// `{ title: '项目工作台', isFullPage: true }`——所以在手机上打开任何一个话题，
// 顶栏都写着「项目工作台」，既不是话题名也不是项目名。
// getRouteHierarchy 是**叶到根**排的（它自己末尾 reverse 过），所以当前页是第一个。
const updateTitle = () => {
  const current = getRouteHierarchy.value.find((item) => item.title)
  currentTitle.value = current?.title ?? t('global.cheese')
}

watch([getRouteHierarchy, () => updateTrigger], updateTitle, { immediate: true })
</script>

<style lang="scss" scoped>
/* 页面填进来的那一格吃掉中间所有剩余宽度；不写 min-width 的话，里面的长标题
   会把右边的头像顶出去。 */
.bar-slot {
  display: flex;
  align-items: center;
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
}

/* Vuetify 的工具栏标题自带 20px/400，比页内页头的标题 (.t-title 15/600) 大一号——
   而在手机上这两条横条是同一条东西在换内容，一页大一号就看得出是两套。加类名
   压过它，而不是去改 .t-title：那一号字是设计 token，页头都读它。 */
.v-toolbar-title.t-title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}
</style>
