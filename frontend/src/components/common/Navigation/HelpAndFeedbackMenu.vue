<script setup lang="ts">
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AccountService from '@/services/account'
import { useFeedbackStore } from '@/stores/feedback'

// 顶栏的「帮助与反馈」入口。**桌面和移动共用这一份** —— 两处各写一遍的话，
// 「管理后台只对管理员出现」这种后加的规则必然只会加进其中一份。
//
// 为什么是**菜单**而不是一颗直达按钮：它底下其实是三个目的地（反馈中心 / 我的反馈 /
// 管理后台），而其中两个今天只能二级跳 —— 先点右上角进反馈中心，再在那一页的页头点
// 第二下。收进一个菜单，三件事都成了一次交互可达，入口本身也从「反馈」两个字变成
// 「帮助与反馈」五个字（需求方原话是「这两个字太不显眼」）。
//
// **代价说清楚**：直达反馈中心从一次点击变成两次（先开菜单）。仓库里那张设计稿自己
// 把这条权衡摆在了明面上（它那两句话是矛盾的），这一版选「接受多点一次、换三个目的地
// 都一级可达」。
//
// **未读点**：`counts.unread` 一直存在、服务端一直在算，但从来没有任何模板读过它。
// 点本身对读屏和色觉障碍读者不成立 —— 所以状态同时写进**可访问名字**里
// （`aria-label` 会是「帮助与反馈，有未读更新」），不只靠一个色点。这条是设计稿的
// 复核专门提出来的：照抄通知铃铛会把铃铛自己的同一个缺陷一起搬过来。
const props = withDefaults(defineProps<{ compact?: boolean }>(), { compact: false })

const store = useFeedbackStore()
const { t } = useI18n()

const hasUnread = computed(() => store.counts.unread > 0)

/** 菜单项。管理后台那一项**只在服务端说我是管理员时**出现 —— `store.isAdmin` 读的是
 *  `/feedback/meta` 的回答，不是前端按 handle 猜的。 */
const items = computed(() => {
  // 使用文档是 nginx 直接发的静态站（/docs/），不在这个应用的路由里，所以走 href
  // 整页跳转而不是 `to`：交给路由器只会落到应用自己的 404。
  const all: { key: string; label: string; to?: string; href?: string }[] = [
    { key: 'docs', href: '/docs/', label: t('navigation.feedback.docs') },
    { key: 'center', to: '/feedback', label: t('navigation.feedback.center') },
    { key: 'mine', to: '/feedback/mine', label: t('navigation.feedback.mine') },
  ]
  if (store.isAdmin) {
    all.push({ key: 'admin', to: '/admin/feedback', label: t('navigation.feedback.admin') })
  }
  // 「了解知是」讲的是这个产品，不是「我」，所以住在这里而不在用户菜单里。
  all.push({ key: 'about', to: '/about', label: t('publicSite.aboutCheese') })
  return all
})

const loggedIn = computed(() => AccountService._loggedIn.value)

/** 问一次「我是谁」与「有多少未读」。
 *
 *  **必须在登录态变化时重问，而不是只在挂载时问一次**：顶栏挂在应用壳上、登录页也
 * 用它，所以它挂载那一刻人往往**还没登录** —— 那一趟 `/feedback/meta` 的回答里
 * `is_admin` 一定是 false，而登录是 SPA 内的一个动作、顶栏不重新挂载。不重问的话，
 * 「管理后台」这一项对管理员**永远不会出现**（真浏览器里就是这么发现的：菜单里只有
 * 两项，而后端明明把他算进了名单）。未读点同理，登录前恒为 0。
 *
 *  两个请求都不等、失败也不报：拿不到就不画点、不显示管理后台那一项，顶栏不该被它们
 *  拖住。 */
function refresh() {
  void store.loadMeta()
  void store.refreshCounts()
}

watch(loggedIn, refresh, { immediate: true })
</script>

<template>
  <v-menu location="bottom end" :offset="8" transition="scale-transition">
    <template #activator="{ props: activator }">
      <v-btn
        v-bind="activator"
        class="help-entry"
        :class="{ 'help-entry--compact': props.compact }"
        variant="text"
        color="on-surface-variant"
        :aria-label="hasUnread ? t('navigation.feedback.unread') : t('navigation.feedback.label')"
      >
        <v-icon size="14" aria-hidden="true">mdi-comment-quote-outline</v-icon>
        <span v-if="!props.compact" class="help-entry__label">{{ t('navigation.feedback.label') }}</span>
        <!-- 和通知铃铛同一个呈现（`v-badge` 的点）。`v-if` + `:model-value` 一起写是照
             铃铛那一处来的：Vuetify 的点在 `model-value` 为真时才画。 -->
        <v-badge v-if="hasUnread" color="error" floating dot :model-value="true" />
      </v-btn>
    </template>

    <v-list class="menu-list" nav density="compact" min-width="160">
      <v-list-item v-for="item in items" :key="item.key" :to="item.to" :href="item.href">
        <v-list-item-title>{{ item.label }}</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-menu>
</template>

<style scoped>
/* 高度写在这里、**不用 `:size`**：数字形式的 `size` 给的是「一个方格」（Vuetify 的
   `useSize` 同时下发 width 和 height），带文字的按钮会被压成正方形、字溢出去 ——
   顶栏那颗「反馈」就这么坏过一次（#1434）。这一条要的是「宽随内容」，高度和顶栏
   这一簇里的其他控件（铃铛、未登录时的语言开关）一样是 28。
   不画描边：这一簇的控件同一种形状（无边、悬停出底色），靠「帮助与反馈」这几个字
   而不是一圈框让它被看见。 */
.help-entry {
  height: 28px;
  padding: 0 8px;
  font-size: 13px;
}

/* 手机上顶栏更窄，只留图标。 */
.help-entry--compact {
  padding: 0 8px;
}

.help-entry__label {
  margin-left: 4px;
}
</style>
