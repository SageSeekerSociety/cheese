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

      <!-- 通知的铃铛不在这儿了：手机上它的去处是底栏「待办」那一格
           (docs/plans/2026-08-18-mobile-shell-design.md §3.3)。 -->

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
          <v-btn icon v-bind="props" variant="text">
            <!-- 没挑过头像的人画彩色首字母，不画 mdi-account：那个图标对每个人
                 都一样，等于告诉你「这是某个人」而不是「这是你」。和左栏
                 (LeftAppRail) 同一套兜底。 -->
            <v-avatar
              size="28"
              :style="userMenu.avatar.value ? undefined : { backgroundColor: userMenu.avatarColor.value }"
            >
              <v-img v-if="userMenu.avatar.value" :src="userMenu.avatar.value">
                <template #error>
                  <span class="bar-avatar-char" :style="{ backgroundColor: userMenu.avatarColor.value }">{{
                    userMenu.avatarInitial.value
                  }}</span>
                </template>
              </v-img>
              <span v-else class="bar-avatar-char">{{ userMenu.avatarInitial.value }}</span>
            </v-avatar>
          </v-btn>
        </template>

        <v-card class="user-menu-card rounded-lg elevation-1 border pa-0" min-width="300">
          <v-card-item class="user-header pa-4 pb-3">
            <v-avatar
              size="56"
              class="mb-2"
              elevation="1"
              :style="userMenu.avatar.value ? undefined : { backgroundColor: userMenu.avatarColor.value }"
            >
              <v-img v-if="userMenu.avatar.value" :src="userMenu.avatar.value">
                <template #error>
                  <span
                    class="bar-avatar-char bar-avatar-char--lg"
                    :style="{ backgroundColor: userMenu.avatarColor.value }"
                    >{{ userMenu.avatarInitial.value }}</span
                  >
                </template>
              </v-img>
              <span v-else class="bar-avatar-char bar-avatar-char--lg">{{ userMenu.avatarInitial.value }}</span>
            </v-avatar>
            <div class="mt-2">
              <v-card-title class="px-0 py-0 text-h6 font-weight-bold">{{ userMenu.nickname.value }}</v-card-title>
              <v-card-subtitle class="px-0 pt-1 pb-0 text-body-2 text-medium-emphasis text-truncate" max-width="220">
                {{ userMenu.intro.value || '还没有个人简介' }}
              </v-card-subtitle>
            </div>
            <div class="d-flex mt-2">
              <v-chip prepend-icon="mdi-account" color="primary" variant="outlined" density="comfortable" size="small">
                UID: {{ userMenu.currentUser.value?.id }}
              </v-chip>
            </div>
          </v-card-item>

          <v-divider></v-divider>

          <v-card-text class="px-4 py-4">
            <v-card variant="tonal" color="primary" class="ai-quota-card rounded-lg mb-3" elevation="0">
              <v-card-text class="pa-3">
                <div class="d-flex align-center mb-2">
                  <!-- surface-bright, not white — see LeftAppRail.vue. -->
                  <v-avatar color="surface-bright" size="28" class="me-2">
                    <v-icon icon="mdi-creation" color="primary" size="small"></v-icon>
                  </v-avatar>
                  <span class="text-subtitle-2 font-weight-medium">知启星 AI</span>
                </div>

                <div class="d-flex justify-space-between align-center text-body-2 mb-2">
                  <span>今日剩余额度</span>
                  <span class="font-weight-medium">
                    {{ userMenu.aiQuota.value?.remaining ?? '-' }}/{{ userMenu.aiQuota.value?.daily ?? '-' }}
                  </span>
                </div>

                <v-progress-linear
                  :model-value="
                    userMenu.aiQuota.value ? (userMenu.aiQuota.value.remaining / userMenu.aiQuota.value.daily) * 100 : 0
                  "
                  color="primary"
                  bg-color="primary-lighten-5"
                  height="4"
                  rounded
                ></v-progress-linear>

                <div class="text-caption mt-1">
                  将在
                  {{ userMenu.aiQuota.value ? userMenu.dayjs(userMenu.aiQuota.value.resetTime).fromNow() : '-' }} 重置
                </div>
              </v-card-text>
            </v-card>

            <v-list class="user-menu-list pa-0" rounded="lg" elevation="0">
              <v-list-item
                :to="{ name: 'UserDefault', params: { id: userMenu.currentUser.value?.id } }"
                rounded="lg"
                class="mb-1"
                color="primary"
              >
                <template #prepend>
                  <v-icon icon="mdi-account" class="me-2"></v-icon>
                </template>
                <v-list-item-title>个人中心</v-list-item-title>
              </v-list-item>
              <ThemeToggle />
              <v-list-item rounded="lg" color="error" @click="userMenu.onLogout">
                <template #prepend>
                  <v-icon icon="mdi-exit-to-app" class="me-2"></v-icon>
                </template>
                <v-list-item-title>退出登录</v-list-item-title>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-menu>

      <!-- 未登录时的登录按钮 -->
      <v-btn v-else-if="!backTo" to="/account/signin" variant="text" prepend-icon="mdi-account">登录</v-btn>
    </template>
  </v-app-bar>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { usePageTitle } from '@/composables/usePageTitle'
import { useUserMenu } from '@/composables/useUserMenu'

import ParentBackButton from './ParentBackButton.vue'

import ThemeToggle from '@/components/common/ThemeToggle.vue'
import { useNavigationStore } from '@/stores/navigation'
import { usePageTitleStore } from '@/stores/title'

// 使用 composables
const userMenu = useUserMenu()

const route = useRoute()
const navigationStore = useNavigationStore()

// 栈末端的路由自己说它回哪儿去（meta.backTo），而不是靠 history.back()——
// 从别处直接打开一个话题链接时，后退会离开这个 app。
const backTo = computed(() => (typeof route.meta.backTo === 'string' ? route.meta.backTo : null))

// 汉堡由路由说了算：一个点了没反应的入口比没有入口更糟，而这条顶栏看不见自己
// 下面挂没挂侧栏——手机上没有侧栏的页面（/inbox、首页那两页）以前照样画一个汉堡。
const hasDrawer = computed(() => route.meta.drawer === true)

// 这一页自己往顶栏里填内容（话题页的标题+阶段、话题列表的项目名、首页那对分段），
// 于是这里不写标题。谁填由路由声明，不靠去数 slot 里有没有东西——那样第一帧
// 永远是空的。
const barSlot = computed(() => route.meta.barSlot === true)
const { updateTrigger } = usePageTitleStore()
const { getRouteHierarchy } = usePageTitle()
const { actionsComponent } = storeToRefs(navigationStore)

const currentTitle = ref('知是社区')

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
  currentTitle.value = current?.title ?? '知是社区'
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
  line-height: 1.4;
  color: var(--ink);
}

/* 没挑过头像时的彩色首字母，同 LeftAppRail 的 .rail-avatar-char。
   #fff 是刻意写死的：底色是 avatarColor() 算出来的那个 #rrggbb，它按固定的
   感知亮度取（OKLCH L = 0.54），深浅两套主题下是同一个值，所以压在它上面的字
   也必须是同一个值 —— 跟着 --v-theme-on-surface 走反而会在两套主题里各错一次。 */
.bar-avatar-char {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  line-height: 1;
  /* stylelint-disable-next-line color-no-hex -- 这是「压在头像底色上」的墨色，
     底色是 avatarColor() 算出来的 #rrggbb（固定感知亮度，深浅两套主题同一个
     值），所以它也必须是同一个值。改成 token 反而会在两套主题里各错一次。
     和 LeftAppRail 的 .rail-avatar-char 是同一处判断。 */
  color: #fff;
  font-weight: 600;
  font-size: 13px;

  &--lg {
    font-size: 22px;
  }
}

.user-menu-card {
  overflow: hidden;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.user-menu-list .v-list-item {
  transition: all 0.2s ease;
  min-height: 44px;
}

.user-menu-list .v-list-item:hover {
  background-color: rgba(var(--v-theme-primary), 0.04);
}

.ai-quota-card {
  transition: all 0.2s ease;
  overflow: hidden;
}
</style>
