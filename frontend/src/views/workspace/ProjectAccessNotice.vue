<script setup lang="ts">
// 「你看不到这个项目」得**留在屏幕上**。
//
// 在这之前，非成员打开项目链接看到的是一个空壳：后端那句话经由一条 4 秒的红条
// 闪过，然后页面上再无任何解释——话题列表空白、项目名不显示，跟「一个刚建好、
// 还什么都没有的项目」长得一模一样。错过那 4 秒就没有第二次机会。
//
// 三档分开写，因为下一步动作不一样：没登录的人要去登录，登录了的人得去要权限，
// 项目归档了的话，所有者可以把它取消归档，别人只能离开。
//
// 谁是所有者（`isOwner`）、服务端那句话（`error`）、正在不在传（`restoring`）都由
// 渲染它的容器算好传进来——这一半只收 props，只发 `restore`。
import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AccessNotice from '@/components/common/AccessNotice.vue'
import { t } from '@/i18n'

defineOptions({ name: 'ProjectAccessNotice' })

const props = withDefaults(
  defineProps<{
    reason: 'unauthenticated' | 'forbidden' | 'archived'
    /** 打开这个项目的人是不是所有者。归档项目只有所有者能把「取消归档」当主操作。 */
    isOwner?: boolean
    /** 上一次「取消归档」失败时服务端那句话；没有就是 null。 */
    error?: string | null
    /** 「取消归档」正在路上。 */
    restoring?: boolean
  }>(),
  { isOwner: false, error: null, restoring: false }
)

defineEmits<{ restore: [] }>()

const said = computed(() => {
  if (props.reason === 'unauthenticated')
    return {
      title: t('work.access.signedOutTitle'),
      // 说清楚登录不一定就够——他可能登录完还是进不来，先说了才不算骗人。
      body: t('work.access.signedOutBody'),
      icon: 'mdi-lock-outline',
      action: { label: t('work.access.signIn'), to: '/account/signin' },
    }
  if (props.reason === 'archived')
    return {
      title: t('work.access.archivedTitle'),
      body: props.isOwner ? t('work.access.archivedOwnerBody') : t('work.access.archivedBody'),
      icon: 'mdi-archive-outline',
      action: { label: t('work.access.backToProjects'), to: '/' },
    }
  return {
    title: t('work.access.forbiddenTitle'),
    // 不写「联系管理员」：这个产品里没有管理员这个角色，指过去等于让人
    // 去找一个不存在的人。
    body: t('work.access.forbiddenBody'),
    icon: 'mdi-lock-outline',
    action: { label: t('work.access.backToProjects'), to: '/' },
  }
})
</script>

<template>
  <AccessNotice :icon="said.icon" :title="said.title" :body="said.body">
    <template #actions>
      <!-- The owner's primary action is to unarchive it; leaving stays the secondary one. -->
      <BaseButton v-if="reason === 'archived' && isOwner" kind="primary" :loading="restoring" @click="$emit('restore')">
        {{ t('work.room.menu.unarchive') }}
      </BaseButton>
      <BaseButton :kind="reason === 'archived' && isOwner ? 'ghost' : 'primary'" :to="said.action.to">
        {{ said.action.label }}
      </BaseButton>
    </template>
    <template #extra>
      <p v-if="error && reason === 'archived'" class="t-body c-danger mt-4">{{ error }}</p>
    </template>
  </AccessNotice>
</template>
