<script setup lang="ts">
// 管理后台「空间申请」**画的那一半**：一列申请、三种状态、通过/驳回、驳回对话框。
//
// 取数（`SpacesApi.reviews` / `review`）与去处在容器 `AdminSpacesPage.vue` 里；这里只吃
// props、只往上发事件。人名那一颗用纯展示的 `UserRef`，名字与去处由容器算好当
// `resolveUser` 递进来（见 `composables/useUserRefResolver`）。
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'
import type { SpaceApplication } from '@/network/api/spaces/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

import AdminFlash from '@/components/admin/AdminFlash.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRef.vue'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  status: string
  items: SpaceApplication[]
  loading: boolean
  saving: boolean
  /** 读这一页失败时是**服务端原话**（原话取不到就空串）；`null` 表示没失败。 */
  loadError: string | null
  /** 通过 / 驳回失败。和读失败分开：重试的不是同一件事。 */
  writeError: string
  offset: number
  hasMore: boolean
  selected: SpaceApplication | null
  reason: string
  resolveUser: (handle: string | null | undefined) => ResolvedUserRef
}>()

const { t } = useI18n()

defineEmits<{
  refresh: []
  'update:status': [status: string]
  previous: []
  next: []
  approve: [item: SpaceApplication]
  reject: [item: SpaceApplication]
  dismissWriteError: []
  dismissReject: []
  confirmReject: []
  'update:reason': [reason: string]
  navigate: [target: ResolvedUserRef['to']]
}>()

const statusOptions = computed(() => [
  { value: 'PENDING', label: t('spaces.review.PENDING') },
  { value: 'APPROVED', label: t('spaces.review.APPROVED') },
  { value: 'REJECTED', label: t('spaces.review.REJECTED') },
])

/** 申请上那句话。`intro` 与 `description` 是建版时同一次填的两个框，一句话常常
 *  两边都有——种子里甚至是「描述就是介绍的开头那句」。两句都画，人读到的是同一句
 *  话在一行申请上说两遍。
 *  所以：谁长留谁，另一句只有在**不互相包含**时才跟在下面（真的说了两件事），
 *  `description` 为空时退回 `intro`。 */
function blurb(item: SpaceApplication): { main: string; extra: string } {
  const one = item.description || ''
  const two = item.intro || ''
  const contains = one && two && (one.includes(two) || two.includes(one))
  if (contains) return { main: one.length >= two.length ? one : two, extra: '' }
  if (!one || !two) return { main: one || two, extra: '' }
  return one.length >= two.length ? { main: one, extra: two } : { main: two, extra: one }
}

/** 头像 URL：`avatarId` 缺失时给空串，`UserAvatar` 自己画彩色首字母。 */
function avatarUrl(avatarId: number | null | undefined): string {
  return avatarId == null ? '' : getAvatarUrl(avatarId)
}

function go(handle: string | null | undefined) {
  return props.resolveUser(handle)
}
</script>

<template>
  <div class="asp">
    <AdminPage :title="t('navigation.admin.spaces')" :sub="t('spaces.review.adminHelp')">
      <template #tools>
        <BaseButton
          icon="mdi-refresh"
          size="sm"
          :aria-label="t('spaces.review.refresh')"
          :loading="loading"
          :disabled="saving"
          @click="$emit('refresh')"
        />
      </template>
      <template #extra>
        <!-- 状态筛选是这一页唯一的筛选器，摆在页头下面：三个值就是三次「我要看哪一堆」。 -->
        <AdminTabs
          :label="t('spaces.review.status')"
          :model-value="status"
          :options="statusOptions"
          @update:model-value="$emit('update:status', $event)"
        />
      </template>

      <div class="asp__body admin-page__body">
        <!-- 通过 / 驳回失败：一条 token 画的横条。驳回框开着时这一句在框里说
             （见下面的对话框），读的人不会去页面上找。 -->
        <AdminFlash
          v-if="writeError && !selected"
          tone="error"
          :text="writeError"
          :dismiss-aria="t('spaces.review.dismiss')"
          @dismiss="$emit('dismissWriteError')"
        />

        <div class="asp__panel">
          <!-- 读失败：中性标题说清是哪一页，服务端原话作说明行，重试就在旁边；**不**画成
               「暂无申请」。判据是 `!== null` 而不是真值：原话取不到时 `loadError` 是空串，
               仍要给出错态。 -->
          <BaseLoadError
            v-if="loadError !== null"
            :title="t('spaces.review.loadFailed')"
            :error="loadError || undefined"
            :retry-label="t('spaces.review.retry')"
            @retry="$emit('refresh')"
          />
          <!-- 首屏（手上一条都没有）画骨架：列表矮、刷新快，一行一行的骨头够了。 -->
          <ul v-else-if="loading && !items.length" class="asp__list" aria-hidden="true">
            <li v-for="i in 5" :key="i" class="asp__row">
              <span class="asp__bone" />
            </li>
          </ul>
          <BaseEmptyState v-else-if="!items.length" size="compact" :title="t('spaces.review.empty')" />
          <ul v-else class="asp__list">
            <li v-for="item in items" :key="item.id" class="asp__row">
              <div class="asp__main">
                <div class="asp__head">
                  <!-- **装饰**：名字就在旁边，头像只是让眼睛在一列里更快找到人。 -->
                  <span class="asp__pfp" aria-hidden="true">
                    <UserAvatar
                      :name="item.owner ?? item.name"
                      :avatar="avatarUrl(item.avatarId)"
                      :size="22"
                      kind="org"
                    />
                  </span>
                  <span class="asp__name" :title="item.name">{{ item.name }}</span>
                  <span v-if="item.reviewStatus !== 'PENDING'" class="asp__chip">
                    {{ item.reviewStatus === 'APPROVED' ? t('spaces.review.APPROVED') : t('spaces.review.REJECTED') }}
                  </span>
                  <span class="asp__meta"
                    >{{ t('spaces.review.applicant') }}：<UserRef
                      v-if="item.owner"
                      :handle="item.owner"
                      :name="go(item.owner).name"
                      :to="go(item.owner).to"
                      @navigate="$emit('navigate', go(item.owner).to)"
                    /><template v-else>—</template></span
                  >
                  <span class="asp__meta" :title="item.createdAt">{{ relTime(item.createdAt) }}</span>
                </div>
                <p v-if="blurb(item).main" class="asp__desc">{{ blurb(item).main }}</p>
                <p v-if="blurb(item).extra" class="asp__desc asp__desc--sub">{{ blurb(item).extra }}</p>
                <p v-if="item.reviewReason" class="asp__meta asp__meta--wrap">
                  {{ t('spaces.review.reason') }}：{{ item.reviewReason }}
                </p>
                <p v-if="item.reviewedBy" class="asp__meta asp__meta--wrap">
                  <UserRef
                    :handle="item.reviewedBy"
                    :name="go(item.reviewedBy).name"
                    :to="go(item.reviewedBy).to"
                    @navigate="$emit('navigate', go(item.reviewedBy).to)"
                  />
                  · {{ item.reviewedAt }}
                </p>
              </div>
              <div v-if="item.reviewStatus === 'PENDING'" class="asp__actions">
                <BaseButton kind="primary" size="sm" :disabled="saving" @click="$emit('approve', item)">
                  {{ t('spaces.review.approve') }}
                </BaseButton>
                <BaseButton size="sm" :disabled="saving" @click="$emit('reject', item)">
                  {{ t('spaces.review.reject') }}
                </BaseButton>
              </div>
            </li>
          </ul>
        </div>

        <!-- 只有一页时不留一排灰按钮：分页控件是「还有别的东西」的意思。 -->
        <div v-if="offset > 0 || hasMore" class="asp__pager">
          <BaseButton size="sm" :disabled="!offset || loading || saving" @click="$emit('previous')">
            {{ t('spaces.review.previous') }}
          </BaseButton>
          <BaseButton size="sm" :disabled="!hasMore || loading || saving" @click="$emit('next')">
            {{ t('spaces.review.next') }}
          </BaseButton>
        </div>
      </div>
    </AdminPage>

    <!-- 驳回：要一句理由 —— 申请的人看不到这句话之外的任何解释。 -->
    <AdaptiveDialog
      :model-value="!!selected"
      :title="t('spaces.review.reject')"
      :cancel-label="t('spaces.create.cancel')"
      :primary-label="t('spaces.review.reject')"
      primary-danger
      :primary-loading="saving"
      :primary-disabled="!reason.trim()"
      :close-disabled="saving"
      @update:model-value="!$event && $emit('dismissReject')"
      @primary="$emit('confirmReject')"
    >
      <p class="asp__who t-body">{{ selected?.name }}</p>
      <AdminFlash v-if="writeError" tone="error" :text="writeError" />
      <v-textarea
        :model-value="reason"
        autocomplete="off"
        :label="t('spaces.review.reason')"
        :disabled="saving"
        rows="3"
        variant="outlined"
        hide-details
        @update:model-value="$emit('update:reason', $event)"
      />
    </AdaptiveDialog>
  </div>
</template>

<style scoped>
/* 根上还挂着对话框，页面本身（`AdminPage`）要拿到整格高度。 */
.asp {
  height: 100%;
}

/* 列表是一张卡：外描边 + 圆角，行与行之间是发丝线。`overflow: hidden` 让首末两行
   自己不去画圆角（这里没有 sticky 表头，不存在 `AdminGrid` 那条坑）。 */
.asp__panel {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
}

.asp__list {
  margin: 0;
  padding: 0;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  list-style: none;
  overflow: hidden;
}

.asp__row {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--line);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.asp__row:last-child {
  border-bottom: 0;
}

@media (hover: hover) and (pointer: fine) {
  .asp__row:hover {
    background: var(--fill);
  }
}

/* 骨架行：和真行同一个高度与内边距，数据到货时不跳。 */
.asp__bone {
  display: block;
  width: 60%;
  height: 14px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
}

/* 正文块。`overflow-wrap: anywhere` 是继承的，作用是让长中文和**无空格文本**也能断
   行——否则一串没断点的字会把 min-content 顶到整句那么宽，父级的 fit-content 宽就跟着
   被顶开（下面窄屏那一处的裁切就是这么来的）。 */
.asp__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.asp__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.asp__pfp {
  display: inline-flex;
  flex: 0 0 auto;
}

.asp__name {
  overflow: hidden;
  max-width: 100%;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 状态徽章：只在看「已通过 / 已驳回」这两堆时出现 —— 待审核那一堆每一条都是待审核，
   每条都盖一个章等于没说。 */
.asp__chip {
  flex: 0 0 auto;
  padding: 2px 8px;
  background: var(--fill);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.asp__meta {
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 驳回原因和审核人那两行是**句子**，不是一格元信息：折行比省略号合适。 */
.asp__meta--wrap {
  white-space: normal;
}

/* 申请里那句话：最多两行。一屏十几条时，谁写了一段话把别人挤到屏外就是它的错。 */
.asp__desc {
  display: -webkit-box;
  overflow: hidden;
  margin: 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.asp__desc--sub {
  color: var(--muted);
}

.asp__actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
}

.asp__pager {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 8px;
}

.asp__who {
  margin: 0 0 12px;
  color: var(--ink);
  font-weight: 600;
}

/* 内容列窄于 700（容器查询挂在后台内容列上，§3.5，不是视口）：一行里的两个按钮会
   把正文挤到一百多像素。动作挪到正文下面，仍然是这一行的
   动作（不与别的行混）。

   横轴在这里要重定一次：改成 `flex-direction: column` 之后 cross 轴变成水平，而上面那条
   `align-items: flex-start` 还在，于是正文块只拿 fit-content 宽；标题又是 `nowrap`，
   把那个宽度顶成整句那么宽，长卡的标题和说明整段从右边被 `.asp__list` 的
   `overflow: hidden` 裁掉，连省略号都看不到。改成 stretch 让正文跟着行宽走，标题在
   窄屏换行（要的是读得全，不是省略号）。行内动作和功能不动。 */
@container admin (max-width: 700px) {
  .asp__body {
    padding: 12px 16px 16px;
  }

  .asp__row {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }

  .asp__name {
    white-space: normal;
  }

  .asp__actions {
    align-self: flex-end;
  }
}
</style>
