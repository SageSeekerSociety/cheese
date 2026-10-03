<script setup lang="ts">
// 「你看不到这个项目」得**留在屏幕上**。
//
// 在这之前，非成员打开项目链接看到的是一个空壳：后端那句话经由一条 4 秒的红条
// 闪过，然后页面上再无任何解释——话题列表空白、项目名不显示，跟「一个刚建好、
// 还什么都没有的项目」长得一模一样。错过那 4 秒就没有第二次机会。
//
// 三档分开写，因为下一步动作不一样：没登录的人要去登录，登录了的人得去要权限，
// 项目归档了的话，所有者可以把它取消归档，别人只能离开。
import { computed, onMounted, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

defineOptions({ name: 'ProjectAccessNotice' })

const props = defineProps<{ reason: 'unauthenticated' | 'forbidden' | 'archived' }>()

const store = useWorkspaceStore()
const isOwner = computed(() => !!store.openedProject?.owner_handle && store.openedProject.owner_handle === myHandle())
const restoring = ref(false)

// 归档了的项目不在项目清单里，谁是所有者要单独问一句。
onMounted(() => {
  if (props.reason === 'archived' && !store.openedProject) void store.loadOpenedProject()
})

async function restore() {
  restoring.value = true
  await store.unarchiveOpenProject()
  restoring.value = false
}

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
      body: isOwner.value ? t('work.access.archivedOwnerBody') : t('work.access.archivedBody'),
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
  <div class="access-notice">
    <v-icon size="40" class="c-faint mb-4">{{ said.icon }}</v-icon>
    <h1 class="t-title mb-2">{{ said.title }}</h1>
    <p class="t-body c-muted mb-6">{{ said.body }}</p>
    <div class="access-notice__actions">
      <!-- 所有者面前主操作是把它取消归档；离开退成次要的那一颗。 -->
      <BaseButton v-if="reason === 'archived' && isOwner" kind="primary" :loading="restoring" @click="restore">
        {{ t('work.room.menu.unarchive') }}
      </BaseButton>
      <BaseButton :kind="reason === 'archived' && isOwner ? 'ghost' : 'primary'" :to="said.action.to">
        {{ said.action.label }}
      </BaseButton>
    </div>
    <p v-if="store.error && reason === 'archived'" class="t-body c-danger mt-4">{{ store.error }}</p>
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

.access-notice__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}
</style>
