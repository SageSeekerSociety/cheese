<template>
  <router-view />

  <v-dialog v-model="isManagingAdmins" width="800">
    <template #default>
      <v-card>
        <v-card-title>{{ t('spaces.detail.manageAdmins') }}</v-card-title>
        <v-card-text>
          <v-list>
            <v-list-subheader>{{ t('spaces.detail.currentAdmins') }}</v-list-subheader>
            <v-list-item
              v-for="admin in space?.admins"
              :key="admin.user.id"
              :title="admin.user.nickname"
              :subtitle="admin.role === 'OWNER' ? t('spaces.detail.owner') : t('spaces.detail.admin')"
            >
              <template #prepend>
                <v-avatar size="36" :image="getAvatarUrl(admin.user.avatarId)" />
              </template>
              <template #append>
                <div class="d-flex align-center">
                  <v-select
                    v-if="admin.role !== 'OWNER' || (admin.user.id !== currentUser?.id && isCurrentUserOwner)"
                    v-model="admin.role"
                    autocomplete="off"
                    :items="adminRoles"
                    density="compact"
                    hide-details
                    class="me-2 admin-role-select"
                    @update:model-value="updateAdminRole(admin.user.id, $event)"
                  />
                  <v-btn
                    v-if="admin.user.id !== currentUser?.id && isCurrentUserOwner"
                    variant="text"
                    color="error"
                    size="small"
                    prepend-icon="mdi-account-remove"
                    @click="confirmRemoveAdmin(admin.user.id, admin.user.nickname)"
                  >
                    {{ t('spaces.detail.unsetAsAdmin') }}
                  </v-btn>
                </div>
              </template>
            </v-list-item>
          </v-list>

          <v-divider class="my-4" />

          <!-- 成员 → 设为管理员：报名的学生默认只是成员，教师角色由创建者按人授予。
               后端只认创建者（OWNER）做这件事，所以这一段整块只对创建者可见。 -->
          <v-list-subheader>{{ t('spaces.detail.members') }}</v-list-subheader>
          <v-list v-if="memberRows.length">
            <v-list-item v-for="member in memberRows" :key="member.userId" :title="member.name">
              <template #prepend>
                <v-avatar size="36" :image="getAvatarUrl(member.avatarId)" />
              </template>
              <template #append>
                <v-btn
                  v-if="isCurrentUserOwner"
                  variant="text"
                  color="primary"
                  size="small"
                  prepend-icon="mdi-account-plus"
                  @click="promoteToAdmin(member.userId)"
                >
                  {{ t('spaces.detail.setAsAdmin') }}
                </v-btn>
              </template>
            </v-list-item>
          </v-list>
          <div v-else class="text-medium-emphasis pa-2">{{ t('spaces.detail.noMembers') }}</div>
        </v-card-text>
        <v-card-actions>
          <v-btn color="primary" @click="closeManageAdmins">{{ t('spaces.detail.close') }}</v-btn>
        </v-card-actions>
      </v-card>
    </template>
  </v-dialog>

  <v-dialog v-model="isEditingProfile" width="800">
    <v-card>
      <v-card-title>{{ t('spaces.detail.editSpaceInfo') }}</v-card-title>
      <v-card-text>
        <v-form>
          <v-container fluid>
            <v-row>
              <v-col cols="12" md="4">
                <avatar-uploader v-model="selectedAvatar" />
              </v-col>
              <v-col cols="12" md="8">
                <v-text-field
                  v-model="name"
                  autocomplete="off"
                  :label="t('spaces.detail.spaceName')"
                  v-bind="nameProps"
                />

                <v-list-subheader>{{ t('spaces.detail.intro') }}</v-list-subheader>
                <v-text-field v-model="intro" autocomplete="off" :counter="255" v-bind="introProps" />

                <template v-if="isCurrentUserAtLeastAdmin">
                  <v-list-subheader>每个发布者对普通用户可见的未结项已通过题目数量上限(M)</v-list-subheader>
                  <v-radio-group v-model="visibleLimitMode" inline hide-details class="mb-2">
                    <v-radio label="无限制" value="unlimited" />
                    <v-radio label="限制数量" value="limited" />
                  </v-radio-group>
                  <v-text-field
                    v-model="visibleTaskLimitInput"
                    type="number"
                    min="0"
                    step="1"
                    label="可见数量上限"
                    density="compact"
                    variant="outlined"
                    :disabled="visibleLimitMode === 'unlimited'"
                    :error-messages="visibleTaskLimitError"
                  />
                </template>
              </v-col>
            </v-row>
          </v-container>
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-btn color="primary" @click="closeUpdating">{{ t('spaces.detail.cancel') }}</v-btn>
        <v-btn color="primary" @click="submitUpdate">{{ t('spaces.detail.update') }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script lang="tsx" setup>
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { toTypedSchema } from '@vee-validate/zod'
import { storeToRefs } from 'pinia'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'
import { getAvatarUrl } from '@/utils/materials'

import { usePageTitle } from '@/composables/usePageTitle'

import AvatarUploader from '@/components/common/AvatarUploader.vue'
import { AvatarsApi } from '@/network/api/avatars'
import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'
import { SpaceAdminRoleType, SpaceAnnouncement, SpaceMember } from '@/types'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))
const TipTapViewer = defineAsyncComponent(() => import('@/components/common/Editor/TipTapViewer.vue'))

const route = useRoute()
const dialog = useDialog()
const { t } = useI18n()
const { setDynamicTitle } = usePageTitle()

const spaceStore = useSpaceStore()
const { closeEditProfile, closeManageAdmins } = spaceStore
const { currentSpace: space, announcements, isEditingProfile, isManagingAdmins } = storeToRefs(spaceStore)

const isAnnouncementCreatingOrUpdating = ref(false)
const updatingAnnouncementIndex = ref<number>()
const newAnnouncementTitle = ref('')
const newAnnouncementContent = ref('')
const newAnnouncementContentEditor = ref<InstanceType<typeof TipTapEditor>>()

const { handleSubmit, defineField, handleReset, resetForm } = useForm({
  validationSchema: toTypedSchema(
    z.object({
      name: z.string().max(32),
      intro: z.string().max(255),
    })
  ),
})

const [name, nameProps] = defineField('name', vuetifyConfig)
const [intro, introProps] = defineField('intro', vuetifyConfig)
const selectedAvatar = ref<File>()
const visibleLimitMode = ref<'limited' | 'unlimited'>('unlimited')
const visibleTaskLimitInput = ref<string | number>('0')
const visibleTaskLimitError = ref('')

// 滑动轮播相关变量
const activeStartIndex = ref(0)
const windowWidth = ref(window.innerWidth)

// 响应式布局 - 根据屏幕宽度确定每行显示的公告数量
const itemsPerRow = computed(() => {
  if (windowWidth.value < 600) return 1 // xs 屏幕 (手机)
  if (windowWidth.value < 960) return 2 // sm 屏幕 (平板)
  if (windowWidth.value < 1264) return 3 // md 屏幕 (笔记本)
  return 4 // lg 和更大屏幕 (桌面)
})

// 确保activeStartIndex始终有效
watchEffect(() => {
  // 如果公告总数小于等于每行显示数量，重置为0
  if (announcements.value.length <= itemsPerRow.value) {
    activeStartIndex.value = 0
  }
  // 否则确保不会超出范围
  else if (activeStartIndex.value > Math.max(0, announcements.value.length - itemsPerRow.value)) {
    activeStartIndex.value = Math.max(0, announcements.value.length - itemsPerRow.value)
  }
})

// 计算当前可见的公告，没有副作用
const visibleAnnouncements = computed(() => {
  // 如果公告总数小于等于每行显示数量，则显示所有公告
  if (announcements.value.length <= itemsPerRow.value) {
    return announcements.value
  }

  const endIndex = Math.min(activeStartIndex.value + itemsPerRow.value, announcements.value.length)
  return announcements.value.slice(activeStartIndex.value, endIndex)
})

// 滑动方法
const slideNext = () => {
  if (activeStartIndex.value < announcements.value.length - itemsPerRow.value) {
    activeStartIndex.value++
  }
}

const slidePrev = () => {
  if (activeStartIndex.value > 0) {
    activeStartIndex.value--
  }
}

// 监听窗口大小变化
onMounted(() => {
  window.addEventListener('resize', updateWindowWidth)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateWindowWidth)
})

const updateWindowWidth = () => {
  windowWidth.value = window.innerWidth
}

const closeUpdating = () => {
  closeEditProfile()
  selectedAvatar.value = undefined
  handleReset()
}

const adminText = computed(() => {
  if (!space.value?.admins?.length) {
    return t('spaces.detail.noAdmins')
  } else if (space.value?.admins?.length === 1) {
    return t('spaces.detail.creator', { creator: space.value?.admins[0].user.nickname })
  } else {
    return t('spaces.detail.creatorAndAdmins', {
      creator: space.value?.admins[0].user.nickname,
      count: space.value?.admins.length - 1,
    })
  }
})

const isCurrentUserAtLeastAdmin = computed(() => {
  const currentUser = AccountService._user.value
  return space.value?.admins?.some((admin) => admin.user.id === currentUser?.id)
})

const getSpace = async (spaceId: number) => {
  await spaceStore.fetchSpace(spaceId)
  spaceStore.fetchCategories()
  resetForm({
    values: {
      name: space.value?.name,
      intro: space.value?.intro,
    },
  })
  if (space.value?.name) {
    setDynamicTitle(space.value?.name, 'SpacesDetail')
  }
  visibleLimitMode.value = space.value?.visibleTaskLimit == null ? 'unlimited' : 'limited'
  visibleTaskLimitInput.value = String(space.value?.visibleTaskLimit ?? 0)
  visibleTaskLimitError.value = ''
}

onMounted(async () => {
  await getSpace(Number(route.params.spaceId))
})

onBeforeRouteUpdate(async (to, from) => {
  if (to.params.spaceId !== from.params.spaceId) {
    await getSpace(Number(to.params.spaceId))
  }
})

const submitUpdate = handleSubmit(async (data) => {
  console.log('submitUpdate', data)
  if (!space.value?.id) {
    return
  }
  try {
    let avatarId = undefined
    if (selectedAvatar.value) {
      const { data: avatarData } = await AvatarsApi.createAvatar(selectedAvatar.value)
      avatarId = avatarData.avatarId
    }
    const visibleTaskLimit = parseVisibleTaskLimit()
    if (visibleTaskLimit === undefined) {
      return
    }
    await SpacesApi.update(space.value.id, {
      name: data.name === space.value.name ? undefined : data.name,
      intro: data.intro,
      avatarId,
      ...(isCurrentUserAtLeastAdmin.value ? { visibleTaskLimit } : {}),
    })
    closeUpdating()
    toast.success(t('spaces.detail.updateSuccess'))
  } catch (error) {
    toast.error(t('spaces.detail.updateFailed'))
  } finally {
    await getSpace(Number(route.params.spaceId))
  }
})

const parseVisibleTaskLimit = () => {
  visibleTaskLimitError.value = ''
  if (visibleLimitMode.value === 'unlimited') {
    return null
  }
  const raw = String(visibleTaskLimitInput.value).trim()
  if (!raw) {
    visibleTaskLimitError.value = '请输入整数 M'
    return undefined
  }
  if (!/^\d+$/.test(raw)) {
    visibleTaskLimitError.value = '仅允许输入大于等于 0 的整数'
    return undefined
  }
  return Number(raw)
}

const getAnnouncementPreview = (content: string, length = 120) => {
  // 从HTML内容中提取纯文本预览
  const textContent = content.replace(/<[^>]*>/g, '').trim()
  return textContent.length > length ? textContent.substring(0, length) + '...' : textContent
}

// 管理员管理相关
const adminRoles = [
  { title: t('spaces.detail.owner'), value: 'OWNER' },
  { title: t('spaces.detail.admin'), value: 'ADMIN' },
]

const currentUser = computed(() => AccountService._user.value)

const isCurrentUserOwner = computed(() => {
  return space.value?.admins?.some((admin) => admin.user.id === currentUser.value?.id && admin.role === 'OWNER')
})

// 板里的成员（学生）——「设为管理员」的候选。管理员本来就不在成员表里（授管理员
// 是往 space_admin_relation 写一行，不是往成员表写），所以这里按 userId 去个重，
// 免得同一个刚被授过权的人在两段列表里各出现一次。
const members = ref<SpaceMember[]>([])

const adminUserIds = computed(() => new Set((space.value?.admins ?? []).map((admin) => admin.user.id)))

const memberRows = computed(() =>
  members.value
    .filter((member) => !adminUserIds.value.has(member.userId))
    .map((member) => ({
      userId: member.userId,
      name: member.user?.nickname || member.user?.username || `#${member.userId}`,
      avatarId: member.user?.avatarId ?? undefined,
    }))
)

const fetchMembers = async () => {
  if (!space.value?.id) {
    members.value = []
    return
  }
  try {
    members.value = (await SpacesApi.listMembers(space.value.id)).data.members
  } catch (error) {
    console.error('加载成员失败:', error)
    members.value = []
  }
}

watch(isManagingAdmins, (open) => {
  if (open) {
    void fetchMembers()
  }
})

// 通道只有一条：后端 /spaces/{id}/managers，且只认创建者（OWNER）。
const promoteToAdmin = async (userId: number) => {
  try {
    await spaceStore.addAdmin(userId, 'ADMIN')
  } catch (error) {
    console.error('设为管理员失败:', error)
  }
}

const updateAdminRole = async (userId: number, role: SpaceAdminRoleType) => {
  try {
    await spaceStore.updateAdmin(userId, role)
  } catch (error) {
    console.error('更新管理员角色失败:', error)
  }
}

const confirmRemoveAdmin = async (userId: number, nickname: string) => {
  const result = await dialog.confirm(t('spaces.detail.confirmRemoveAdmin', { nickname })).wait()
  if (!result) {
    return
  }

  try {
    await spaceStore.removeAdmin(userId)
  } catch (error) {
    console.error('移除管理员失败:', error)
  }
}
</script>

<style scoped lang="scss">
.admin-info-container {
  display: flex;
  align-items: center;
}

.admin-avatars {
  display: flex;
  flex-wrap: nowrap;
  overflow: hidden;

  .admin-avatar {
    /* 叠放头像之间的分隔环 = 它们背后的面色 */
    border: 2px solid var(--surface);
    transition: margin-left 0.3s ease;

    &:not(:first-child) {
      margin-left: -12px;
    }
  }

  &:hover .admin-avatar:not(:first-child) {
    margin-left: -8px;
  }

  .admin-avatar {
    position: relative;

    &:nth-child(1) {
      z-index: 5;
    }

    &:nth-child(2) {
      z-index: 4;
    }

    &:nth-child(3) {
      z-index: 3;
    }

    &:nth-child(4) {
      z-index: 2;
    }

    &:nth-child(5) {
      z-index: 1;
    }
  }
}

.admin-text {
  margin-left: 12px;
  white-space: nowrap;
}

.admin-role-select {
  max-width: 140px;
}

.floating-add-btn {
  position: absolute;
  top: -16px;
  right: 16px;
  z-index: 10;
  border-radius: 50%;
  box-shadow: var(--shadow-1);
}

.cursor-pointer {
  cursor: pointer;
}
</style>
