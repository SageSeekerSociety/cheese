<script setup lang="ts">
// 「你看不到这个项目」得**留在屏幕上**。
//
// 在这之前，非成员打开项目链接看到的是一个空壳：后端那句话经由一条 4 秒的红条
// 闪过，然后页面上再无任何解释——话题列表空白、项目名不显示，跟「一个刚建好、
// 还什么都没有的项目」长得一模一样。错过那 4 秒就没有第二次机会。
//
// 两档分开写，因为下一步动作不一样：没登录的人要去登录，登录了的人得去要权限。
// 合成一句「无权访问」等于对两种人都说不出他该干什么。
import { computed } from 'vue'

import { t } from '@/i18n'

defineOptions({ name: 'ProjectAccessNotice' })

const props = defineProps<{ reason: 'unauthenticated' | 'forbidden' }>()

// 取词在 computed 里做，切语言时这句跟着变：模块级取词函数读的是 locale 这个
// ref，computed 就把它当依赖收下了。
const said = computed(() =>
  props.reason === 'unauthenticated'
    ? {
        title: t('workspace.access.signedOutTitle'),
        // 说清楚登录不一定就够——他可能登录完还是进不来，先说了才不算骗人。
        body: t('workspace.access.signedOutBody'),
        action: { label: t('workspace.access.signedOutAction'), to: '/account/signin' },
      }
    : {
        title: t('workspace.access.forbiddenTitle'),
        // 不写「联系管理员」：这个产品里没有管理员这个角色，指过去等于让人
        // 去找一个不存在的人。
        body: t('workspace.access.forbiddenBody'),
        action: { label: t('workspace.access.forbiddenAction'), to: '/' },
      }
)
</script>

<template>
  <div class="access-notice">
    <v-icon size="40" class="c-faint mb-4">mdi-lock-outline</v-icon>
    <h1 class="t-title mb-2">{{ said.title }}</h1>
    <p class="t-body c-muted mb-6">{{ said.body }}</p>
    <v-btn color="primary" variant="flat" :to="said.action.to">{{ said.action.label }}</v-btn>
  </div>
</template>

<style scoped>
.access-notice {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 24px;
  text-align: center;
}

.access-notice p {
  max-width: 32em;
}
</style>
