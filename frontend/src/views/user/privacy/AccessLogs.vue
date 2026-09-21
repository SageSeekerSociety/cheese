<template>
  <div class="access-logs">
    <div class="d-flex justify-space-between align-center mb-6">
      <h2 class="text-h5 font-weight-medium mb-0">{{ t('users.privacy.accessLogs.title') }}</h2>
      <v-chip color="info" variant="tonal" size="small" class="privacy-status-chip" prepend-icon="mdi-history">
        {{ t('users.privacy.accessLogs.count', { count: paging.customParams.value.total || 0 }) }}
      </v-chip>
    </div>

    <p class="text-body-2 text-medium-emphasis mb-6">
      {{ t('users.privacy.accessLogs.intro') }}
    </p>

    <!-- 数据表格 -->
    <v-card variant="flat" rounded="lg" class="mb-6 access-table">
      <infinite-scroll
        :loading="paging.loadingMore.value"
        :has-more="paging.hasMore.value"
        :initial-loading="paging.refreshing.value && !paging.data.value.length"
        :is-empty="!paging.data.value.length"
        :force-manual="true"
        @load-more="loadMore"
      >
        <!-- 自定义表格 -->
        <div class="custom-table">
          <!-- 表头 -->
          <div class="table-header d-flex">
            <div class="table-cell accessor">{{ t('users.privacy.accessLogs.colAccessor') }}</div>
            <div class="table-cell access-time">{{ t('users.privacy.accessLogs.colAccessTime') }}</div>
            <div class="table-cell access-type">{{ t('users.privacy.accessLogs.colAccessType') }}</div>
            <div class="table-cell access-entity">{{ t('users.privacy.accessLogs.colAccessEntity') }}</div>
            <div class="table-cell ip-address">{{ t('users.privacy.accessLogs.colIpAddress') }}</div>
          </div>

          <!-- 表格内容 -->
          <div v-for="(item, index) in paging.data.value" :key="index" class="table-row d-flex">
            <!-- 访问者 -->
            <div class="table-cell accessor">
              <div class="d-flex align-center">
                <v-avatar size="32" class="mr-2">
                  <v-img :src="getAvatarUrl(item.accessor.avatarId)" />
                </v-avatar>
                <span>{{ item.accessor.nickname }}</span>
              </div>
            </div>

            <!-- 访问时间 -->
            <div class="table-cell access-time">
              {{ formatDate(item.accessTime) }}
            </div>

            <!-- 访问类型 -->
            <div class="table-cell access-type">
              <v-chip :color="accessTypeColor(item.accessType)" size="small" variant="tonal" class="font-weight-medium">
                {{ accessTypeText(item.accessType) }}
              </v-chip>
            </div>

            <!-- 访问目的 -->
            <div class="table-cell access-entity">
              <div class="d-flex align-center">
                <v-icon
                  :icon="accessModuleIcon(item.accessModuleType)"
                  size="small"
                  class="mr-1"
                  :color="accessModuleColor(item.accessModuleType)"
                ></v-icon>
                <span>{{ accessEntityText(item) }}</span>
              </div>
            </div>

            <!-- IP地址 -->
            <div class="table-cell ip-address">
              <div class="d-flex align-center">
                <v-icon icon="mdi-ip-network" size="small" class="mr-1 text-medium-emphasis"></v-icon>
                <span class="text-body-2">{{ item.ipAddress }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 加载中状态 -->
        <template #loading>
          <div class="d-flex justify-center py-3">
            <v-progress-circular indeterminate size="24" width="2" color="primary"></v-progress-circular>
            <span class="text-body-2 ml-2">{{ t('users.privacy.accessLogs.loadingMore') }}</span>
          </div>
        </template>

        <!-- 没有更多数据 -->
        <template #no-more>
          <div class="d-flex justify-center py-3">
            <v-icon icon="mdi-check-circle" size="small" color="success" class="mr-1"></v-icon>
            <span class="text-body-2 text-medium-emphasis">{{ t('users.privacy.accessLogs.allLoaded') }}</span>
          </div>
        </template>

        <!-- 空状态 -->
        <template #empty>
          <div class="d-flex flex-column align-center py-8">
            <v-icon icon="mdi-shield-check" size="56" color="success" class="mb-3"></v-icon>
            <span class="text-h6 mb-1">{{ t('users.privacy.accessLogs.empty') }}</span>
            <span class="text-body-2 text-medium-emphasis">{{ t('users.privacy.accessLogs.emptyHint') }}</span>
          </div>
        </template>

        <!-- 加载状态 -->
        <template #skeleton>
          <div class="d-flex flex-column align-center py-8">
            <v-progress-circular indeterminate color="primary" size="48" class="mb-3"></v-progress-circular>
            <div class="text-body-1">{{ t('users.privacy.accessLogs.loading') }}</div>
          </div>
        </template>
      </infinite-scroll>
    </v-card>

    <!-- 隐私提示 -->
    <v-alert
      type="info"
      variant="tonal"
      class="mt-4"
      border="start"
      density="comfortable"
      icon="mdi-information-outline"
    >
      <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('users.privacy.accessLogs.noticeTitle') }}</div>
      <p class="text-body-2 mb-0">
        <i18n-t keypath="users.privacy.accessLogs.noticeBody" scope="global" tag="span">
          <template #contact>
            <a href="#" class="text-decoration-none">{{ t('users.privacy.accessLogs.contactUs') }}</a>
          </template>
        </i18n-t>
      </p>
    </v-alert>
  </div>
</template>

<script lang="ts" setup>
import type {
  UserIdentityAccessLog,
  UserIdentityAccessModuleType,
  UserIdentityAccessType,
} from '@/network/api/users/types'

import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import { getAvatarUrl } from '@/utils/materials'
import { usePaging } from '@/utils/paging'

import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
import { UserApi } from '@/network/api/users'
import { currentUserId } from '@/services/account'

const { t } = useI18n()

const pageSize = 10

const fetchLogs = async (pageStart?: number, customParams?: LogsParams) => {
  if (!currentUserId.value) return { data: [], page: { pageStart: 0, pageSize: 0, nextStart: 0, hasMore: false } }

  try {
    const { data } = await UserApi.getRealNameAccessLogs(currentUserId.value, pageStart, pageSize)

    // 更新总数
    if (data.page.total && customParams) {
      customParams.total = data.page.total
    }

    return {
      data: data.logs,
      page: {
        pageStart: pageStart || 0,
        pageSize: pageSize,
        nextStart: data.page.nextStart,
        hasMore: data.page.hasMore,
        total: data.page.total,
      },
    }
  } catch (error) {
    console.error('获取访问记录失败', error)
    return { data: [], page: { pageStart: 0, pageSize: 0, nextStart: 0, hasMore: false } }
  }
}

interface LogsParams {
  total?: number
}

const paging = usePaging<UserIdentityAccessLog, LogsParams>(fetchLogs, undefined, { total: 0 })

// 加载更多记录
const loadMore = () => {
  paging.loadMore()
}

const formatDate = (date: string | Date | number) => {
  return dayjs(date).format('YYYY-MM-DD HH:mm')
}

// 访问类型文本
const accessTypeText = (type: UserIdentityAccessType) => {
  switch (type) {
    case 'VIEW':
      return t('users.privacy.accessLogs.typeView')
    case 'EXPORT':
      return t('users.privacy.accessLogs.typeExport')
    default:
      return t('users.privacy.accessLogs.typeUnknown')
  }
}

// 访问类型颜色
const accessTypeColor = (type: UserIdentityAccessType) => {
  switch (type) {
    case 'VIEW':
      return 'info'
    case 'EXPORT':
      return 'warning'
    default:
      return 'grey'
  }
}

// 访问模块图标
const accessModuleIcon = (type?: UserIdentityAccessModuleType) => {
  switch (type) {
    case 'TASK':
      return 'mdi-clipboard-text-outline'
    default:
      return 'mdi-account-check-outline'
  }
}

// 访问模块颜色
const accessModuleColor = (type?: UserIdentityAccessModuleType) => {
  switch (type) {
    case 'TASK':
      return 'primary'
    default:
      return 'grey'
  }
}

// 访问实体文本
const accessEntityText = (log: UserIdentityAccessLog) => {
  if (log.accessEntityName) {
    return `${log.accessEntityName}`
  }

  switch (log.accessModuleType) {
    case 'TASK':
      return t('users.privacy.accessLogs.entityTask')
    default:
      return t('users.privacy.accessLogs.entityVerify')
  }
}

onMounted(() => {
  paging.refresh()
})
</script>

<style scoped lang="scss">
.access-logs {
  .access-table {
    background-color: var(--surface);
    border: 1px solid rgba(var(--v-border-color), 0.12);
    border-radius: 8px;
    overflow: hidden;

    .custom-table {
      width: 100%;

      .table-header {
        background-color: rgba(var(--v-theme-primary), 0.05);
        font-weight: 500;
        border-bottom: 1px solid rgba(var(--v-border-color), 0.12);
      }

      .table-row {
        border-bottom: 1px solid rgba(var(--v-border-color), 0.08);
        transition: background-color 0.2s ease;

        &:hover {
          background-color: rgba(var(--v-theme-primary), 0.03);
        }

        &:last-child {
          border-bottom: none;
        }
      }

      .table-cell {
        padding: 12px 16px;
        display: flex;
        align-items: center;
      }

      .accessor {
        width: 25%;
      }

      .access-time {
        width: 20%;
      }

      .access-type {
        width: 12%;
      }

      .access-entity {
        width: 25%;
      }

      .ip-address {
        width: 18%;
      }
    }
  }

  .privacy-status-chip {
    font-weight: 500;
  }
}
</style>
