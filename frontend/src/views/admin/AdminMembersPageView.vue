<script setup lang="ts">
// 管理后台「成员管理」**画的那一半**：两份名单、六列表格、加人对话框、移出确认框。
//
// 这一页的规矩（名单为什么分两块、为什么搜账号走服务端、为什么确认框正文用
// `<i18n-t>`、六列的定宽怎么来的）写在容器 `AdminMembersPage.vue` 顶上那段注释里 ——
// 那些是**数据来的地方**的事。这里只管画：契约那一行怎么摊平成一行、哪一列在窄屏卡片
// 里收起来、空态和「读不到」分别长什么样。
//
// 取数（`listPlatformAdmins` / `searchAdminCandidates` / `addPlatformAdmin` /
// `removePlatformAdmin`）全在容器里；这里只吃 props、只往上发事件。人名那一颗用纯展示
// 的 `UserRef`，名字与去处由容器算好当 `resolveUser` 递进来（见
// `composables/useUserRefResolver`）。
import type { PlatformAdminRow, PlatformAdminsPayload } from '@/api'
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'
import type { AdminCandidate } from '@/types/admin'

import { computed, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

import AdminFlash from '@/components/admin/AdminFlash.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import BaseTable from '@/components/base/BaseTable.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRef.vue'
import { relTime } from '@/lib/relTime'

defineOptions({ name: 'AdminMembersPageView' })

const props = defineProps<{
  /** 服务端给的两块名单，`null` = 还没拿到（骨架 / 读失败）。 */
  roster: PlatformAdminsPayload | null
  loading: boolean
  /** 上一次失败**原话**；`null` 表示没失败。 */
  error: string | null
  /** 加/删成功之后的**结果**；`null` 表示这次没有要说的。 */
  notice: string | null
  dialogOpen: boolean
  /** 加人框输入框里那串字（搜索在服务端，防抖与请求都在容器里）。 */
  search: string
  candidates: AdminCandidate[]
  searching: boolean
  selected: AdminCandidate[]
  adding: boolean
  /** 正在删的那一行（`handle`）。 */
  removing: string | null
  confirmHandle: string | null
  resolveUser: (handle: string | null | undefined) => ResolvedUserRef
}>()

const emit = defineEmits<{
  reload: []
  openDialog: []
  'update:dialogOpen': [open: boolean]
  'update:search': [search: string]
  'update:selected': [selected: AdminCandidate[]]
  submit: []
  'update:confirmHandle': [handle: string | null]
  remove: []
  dismissNotice: []
  navigate: [target: ResolvedUserRef['to']]
}>()

const { t } = useI18n()

/** 画一行要的那几样，从契约那一行摊平 —— 模板里存不下中间结果，摊平一次比在模板里
 *  对同一个函数调三遍清楚。 */
interface RowView {
  handle: string
  /** 传给 `UserAvatar` 的 `avatar`。为 null = 从没挑过头像，**必须**走彩色首字母
   *  （理由在 api.ts 与那个组件顶上）。 */
  avatarId: number | null
  /** 主行那串字：昵称；没有昵称时是 handle。 */
  primary: string
  /** 仅在它和昵称**不是同一个串**时画。否则为 null —— 同一个串画两遍是这一版要修的
   *  毛病之一。 */
  secondary: string | null
  /** 被 ellipsis 截断时 `title` 要拿到的全串。 */
  label: string
  /** 平台上有没有（活着的）这个账号：false = 这行是死权限，状态列明画出来。 */
  hasAccount: boolean
  /** ISO 串；`hasAccount` 为 false 时是 null —— 注册时间格画 `—`（没读到不画 0）。 */
  registeredAt: string | null
  /** agent 做不了管理动作，所以这行权限用不上 —— who 列挂徽章说明。 */
  isAgent: boolean
}

/** 页面上加的那一组多两格出处。 */
interface AddedRowView extends RowView {
  addedBy: string
  createdAt: string
}

/** 头像 URL。`avatar_id` 为 null 时给**空串**而不是 `getAvatarUrl(null)`：后者回的是
 *  `/avatars/default`，也就是所有没挑过头像的人共用同一张脸 —— 那比按 handle 派生的
 *  彩色首字母更难把人分辨开，而分辨人正是头像唯一的活（`UserAvatar` 的约定）。 */
function avatarUrl(avatarId: number | null): string {
  return avatarId == null ? '' : getAvatarUrl(avatarId)
}

/** 表体画真行还是画「读不到」。名单本身**没有整表空态**：根那两组的分组行永远在，
 *  组里空着的时候由组内那一行说明（`members.empty.*`）。 */
const gridState = computed<'rows' | 'error'>(() => (props.error && !props.roster ? 'error' : 'rows'))

function toRowView(row: PlatformAdminRow): RowView {
  // `nickname` 为 null（平台上没有这个账号 / 没 profile 行）或本来就等于 handle 时，
  // 主行只有 handle 一个串可画 —— 判据是「两串相不相同」，不是「昵称有没有值」。
  const nick = row.nickname
  const primary = nick !== null && nick !== row.handle ? nick : row.handle
  const secondary = nick !== null && nick !== row.handle ? row.handle : null
  return {
    handle: row.handle,
    avatarId: row.avatar_id,
    primary,
    secondary,
    label: secondary ? `${primary} ${secondary}` : primary,
    hasAccount: row.has_account,
    registeredAt: row.registered_at,
    isAgent: row.is_agent,
  }
}

const rootRows = computed<RowView[]>(() => (props.roster?.root ?? []).map(toRowView))
const addedRows = computed<AddedRowView[]>(() =>
  (props.roster?.added ?? []).map((row) => ({
    ...toRowView(row),
    addedBy: row.added_by_handle,
    createdAt: row.created_at,
  }))
)

/** 六条列。**只有「添加信息」那一列是 `null`**（自适应）—— `table-layout: fixed`
 *  下没有宽度的列会平分剩余空间，多给一列就散架。第一列要装下头像 + 昵称 + handle
 *  （+ 可能的 agent 徽章），300px 够（超长的由 ellipsis 收，`title` 里拿全文）。
 *  定宽合计 770px，表格下限收到 1000（见样式里那条 `--agrid-min`）时自适应列
 *  还拿得到 230px。 */
const COLS: (string | null)[] = ['300px', '140px', '100px', '110px', null, '120px']
const BONE_WIDTHS = ['58%', '44%', '52%', '40%', '64%', '42%']

const countLine = computed(() =>
  props.roster ? t('members.toolbar.count', { count: rootRows.value.length + addedRows.value.length }) : ''
)

/** 打开确认框的那颗「移出」。**自己记一下**，因为 Vuetify 的 `VDialog` 在没有
 *  `activator`（这里是 `v-model` 驱动的，没有触发元素插槽）时不会把焦点还回去 ——
 *  关掉之后焦点落到 `body`，键盘用户被扔回页面开头，得重新 Tab 一路找回来。 */
let removeTrigger: HTMLElement | null = null

function askRemove(event: Event, handle: string) {
  removeTrigger = event.currentTarget as HTMLElement | null
  emit('update:confirmHandle', handle)
}

// 确认框关掉（取消 / Esc / 点外面 / 移出成功）时把焦点送回触发它的那颗按钮。
// **移出成功那一支不送**：那一行连同按钮已经没了（`isConnected` 为假），focus 一个
// 脱离文档的节点是空操作，还不如留给 Vuetify 自己的收尾。
watch(
  () => props.confirmHandle,
  async (now, was) => {
    if (!was || now) return
    await nextTick()
    if (removeTrigger?.isConnected) removeTrigger.focus()
  }
)

function go(handle: string | null | undefined) {
  return props.resolveUser(handle)
}
</script>

<template>
  <div class="am">
    <AdminPage :title="t('navigation.admin.members')" :sub="t('members.header.subtitle')">
      <template #tools>
        <span class="t-meta-read t-num am__count">{{ countLine }}</span>
        <BaseButton
          icon="mdi-refresh"
          size="sm"
          :aria-label="t('members.toolbar.refresh')"
          :loading="loading"
          @click="emit('reload')"
        />
        <!-- 全页唯一一块琥珀：这一页确实有一个主操作，而它就是这个。 -->
        <BaseButton kind="primary" size="sm" prepend-icon="mdi-account-plus-outline" @click="emit('openDialog')">
          {{ t('members.toolbar.add') }}
        </BaseButton>
      </template>

      <div class="am__body admin-page__body">
        <!-- 读不到名单时**不在这里说话**：那一条画在表格自己的位置上（列头下面、
             重试按钮就在旁边）。两处说同一件事，人会以为是两次失败。 -->
        <AdminFlash v-if="error && roster" tone="error" :text="error" />
        <AdminFlash
          v-else-if="notice"
          tone="ok"
          :text="notice"
          :dismiss-aria="t('members.notice.dismiss')"
          @dismiss="emit('dismissNotice')"
        />

        <div class="am__gridwrap">
          <BaseTable
            :label="t('members.table.label')"
            :cols="COLS"
            :bone-widths="BONE_WIDTHS"
            :loading="loading && !roster"
            :busy="loading && roster !== null"
            :state="gridState"
            :skeleton-rows="4"
            cards
          >
            <template #head>
              <tr>
                <th scope="col">{{ t('members.table.colMember') }}</th>
                <th scope="col">{{ t('members.table.colStatus') }}</th>
                <th scope="col">{{ t('members.table.colRegistered') }}</th>
                <th scope="col">{{ t('members.table.colSource') }}</th>
                <th scope="col">{{ t('members.table.colAddedInfo') }}</th>
                <th scope="col" class="am__num">{{ t('members.table.colActions') }}</th>
              </tr>
            </template>

            <!-- 读失败那一条就画在列头下面：位置说明「这张表没读出来」，而重试按钮
                 就在原因旁边。中性标题说清是哪一页，服务端原话落到说明行 —— 原话当标题会被
                 长句撑得不像标题，也把「是哪一页出的事」盖掉了。原话取不到时（`error` 里
                 只剩兜底那句）不再重复一遍标题。 -->
            <template #error>
              <BaseLoadError
                :title="t('members.error.loadFailed')"
                :error="error && error !== t('members.error.loadFailed') ? error : undefined"
                :retry-label="t('members.error.retry')"
                @retry="emit('reload')"
              />
            </template>

            <!-- 组头行。**名字和人数是两个元素**，不是一个字符串拼出来的：它们是两种
             东西（这一组叫什么 / 有几个人），拼在一起会让读屏把它们念成一串数字
             加名字，也让人没法单独抓那个数。
             `role="rowheader"` 让读屏把这一格当成表头，而不是一个普通单元格。人数后面
             藏了一个单位补字：屏幕上「2」紧挨着组名（「部署配置里的根管理员 2」），
             看得出来是什么的数；读屏顺着格子念时却只剩一个光秃秃的数字。
             **不改 `<th>`**：表壳只给 `tbody td` 内边距，换元素就得在这里把表壳那份
             几何抄一遍，而抄一份就会和表壳飘开。 -->
            <tr class="am__group" data-card="flat">
              <td colspan="6" class="am__groupcell" role="rowheader">
                <span class="am__grouplabel">{{ t('members.group.root') }}</span>
                <span class="am__groupcount">
                  {{ rootRows.length }}<span class="visually-hidden">{{ t('members.group.personUnit') }}</span>
                </span>
              </td>
            </tr>

            <tr v-for="row in rootRows" :key="row.handle" class="am__row">
              <td class="am__cell" data-card="primary" :title="row.label">
                <span class="am__who">
                  <!-- **装饰**：名字就在旁边，头像只是让眼睛在一列里更快找到人。包一层
                   `aria-hidden` 而不是把属性透传给 `UserAvatar` —— 它的根是组件，
                   属性不保证落到底层的 `<img>`/`<div>` 上。 -->
                  <span class="am__pfp" aria-hidden="true">
                    <CheeseAvatar
                      v-if="row.isAgent"
                      class="am__avatar"
                      :name="row.primary"
                      :handle="row.handle"
                      :size="20"
                    />
                    <UserAvatar
                      v-else
                      class="am__avatar"
                      :name="row.handle"
                      :avatar="avatarUrl(row.avatarId)"
                      :size="20"
                    />
                  </span>
                  <span class="am__name-main">{{ row.primary }}</span>
                  <span v-if="row.secondary" class="am__name-sub">{{ row.secondary }}</span>
                  <!-- agent 徽章跟在名字后面：它说的不是状态（状态列在右边），是「这个人
                   是什么」。flex 子项的 min-width:auto 保住它不被长昵称挤没。 -->
                  <span v-if="row.isAgent" class="chip-neutral" :title="t('members.row.agentBadgeTitle')">
                    {{ t('members.row.agentBadge') }}
                  </span>
                </span>
              </td>
              <!-- 状态列「异常才说话」：正常行画 `—`，不把整列刷成一片「正常」的噪音。
               窄屏卡片里那一格**整格收起来**（`data-card="hide"`）：卡片上多一行
               「账号状态 —」是没有信息的行，而异常那一行照样画得出来。 -->
              <td
                class="am__cell"
                :data-card="row.hasAccount ? 'hide' : undefined"
                :data-label="t('members.table.colStatus')"
              >
                <span v-if="row.hasAccount" class="am__dim">—</span>
                <span v-else class="am__warn" :title="t('members.row.noAccountTitle')">{{
                  t('members.row.noAccount')
                }}</span>
              </td>
              <td
                v-if="row.registeredAt"
                class="am__cell"
                :data-label="t('members.table.colRegistered')"
                :title="relTime(row.registeredAt)"
              >
                <span class="am__dim">{{ relTime(row.registeredAt) }}</span>
              </td>
              <td v-else class="am__cell" data-card="hide"><span class="am__dim">—</span></td>
              <!-- 来源那一列在卡片里收起来：这一组的组头刚说过「部署配置里的根管理员」，
               卡片上再写一遍是同一句话说两次。 -->
              <td class="am__cell" data-card="hide">
                <span class="am__dim">{{ t('members.row.sourceRoot') }}</span>
              </td>
              <td class="am__cell" data-card="hide"><span class="am__dim">—</span></td>
              <!-- 这一格上一版是空的，人要读完上面那段说明才知道「不是漏画了按钮」。
               写出来比留白省一次阅读。 -->
              <td class="am__cell am__cell--actions" :data-label="t('members.table.colActions')">
                <span class="am__dim">{{ t('members.row.notRemovable') }}</span>
              </td>
            </tr>
            <tr v-if="!rootRows.length" class="am__row" data-card="flat">
              <td colspan="6" class="am__cell am__none">{{ t('members.empty.root') }}</td>
            </tr>

            <tr class="am__group" data-card="flat">
              <td colspan="6" class="am__groupcell" role="rowheader">
                <span class="am__grouplabel">{{ t('members.group.added') }}</span>
                <span class="am__groupcount">
                  {{ addedRows.length }}<span class="visually-hidden">{{ t('members.group.personUnit') }}</span>
                </span>
              </td>
            </tr>

            <tr v-for="row in addedRows" :key="row.handle" class="am__row">
              <td class="am__cell" data-card="primary" :title="row.label">
                <span class="am__who">
                  <span class="am__pfp" aria-hidden="true">
                    <CheeseAvatar
                      v-if="row.isAgent"
                      class="am__avatar"
                      :name="row.primary"
                      :handle="row.handle"
                      :size="20"
                    />
                    <UserAvatar
                      v-else
                      class="am__avatar"
                      :name="row.handle"
                      :avatar="avatarUrl(row.avatarId)"
                      :size="20"
                    />
                  </span>
                  <span class="am__name-main">{{ row.primary }}</span>
                  <span v-if="row.secondary" class="am__name-sub">{{ row.secondary }}</span>
                  <span v-if="row.isAgent" class="chip-neutral" :title="t('members.row.agentBadgeTitle')">
                    {{ t('members.row.agentBadge') }}
                  </span>
                </span>
              </td>
              <td
                class="am__cell"
                :data-card="row.hasAccount ? 'hide' : undefined"
                :data-label="t('members.table.colStatus')"
              >
                <span v-if="row.hasAccount" class="am__dim">—</span>
                <span v-else class="am__warn" :title="t('members.row.noAccountTitle')">{{
                  t('members.row.noAccount')
                }}</span>
              </td>
              <td
                v-if="row.registeredAt"
                class="am__cell"
                :data-label="t('members.table.colRegistered')"
                :title="relTime(row.registeredAt)"
              >
                <span class="am__dim">{{ relTime(row.registeredAt) }}</span>
              </td>
              <td v-else class="am__cell" data-card="hide"><span class="am__dim">—</span></td>
              <td class="am__cell" data-card="hide">
                <span class="am__dim">{{ t('members.row.sourceAdded') }}</span>
              </td>
              <!-- 添加信息在卡片里**留着**：它是这一行唯一回答「谁加的、什么时候」的地方，
               而这一组里每一行都不一样。 -->
              <td
                class="am__cell"
                :data-label="t('members.table.colAddedInfo')"
                :title="t('members.row.addedBy', { by: row.addedBy, time: relTime(row.createdAt) })"
              >
                <i18n-t keypath="members.row.addedBy" tag="span" class="am__dim">
                  <template #by>
                    <UserRef
                      :handle="row.addedBy"
                      :name="go(row.addedBy).name"
                      :to="go(row.addedBy).to"
                      @navigate="$emit('navigate', go(row.addedBy).to)"
                    />
                  </template>
                  <template #time>{{ relTime(row.createdAt) }}</template>
                </i18n-t>
              </td>
              <td class="am__cell am__cell--actions" :data-label="t('members.table.colActions')">
                <!-- 描边（不是实心、不是文字按钮）：这个动作改的是「谁能看别人的私密
                 反馈」，它得看得出来是一个真正界内的按钮，而不是一行可以随手划过的
                 文字。确认在同名的对话框里。（**没有 `aria-label`** —— 加了会把可及
                 名字覆盖掉，读屏念的就不再是「移出」这两个字了。）
                 颜色**留中性**（不写 `color`）：每行一颗红按钮会把这张表变吵，而这一页
                 的琥珀是「添加管理员」；破坏性那一颗的红留给确认框里那一颗。 -->
                <BaseButton size="sm" :loading="removing === row.handle" @click="askRemove($event, row.handle)">
                  {{ t('members.row.remove') }}
                </BaseButton>
              </td>
            </tr>
            <tr v-if="!addedRows.length" class="am__row" data-card="flat">
              <td colspan="6" class="am__cell am__none">{{ t('members.empty.added') }}</td>
            </tr>
          </BaseTable>
        </div>
      </div>
    </AdminPage>

    <!-- 加人的框照「成员页面邀请」那一套：按钮 → 对话框 → 可搜的多选 + 添加。
         区别只有一个，是数据来源：那边的人选是一次拉回来的项目成员，这里上千个账号，
         所以搜索走服务端（`/admin/users?q=`）。

         `:no-filter="true"` 是这件事的**另一半**，不加就等于没做（同 TopicSelector）：
         `v-autocomplete` 默认还会拿输入串再筛一次自己的 `items`，而筛的是
         `item-title` —— 这里正是 handle。于是**按昵称搜不出人**：服务端把「彭文博」
         查成了 `pengwenbo` 并回了一行，组件再拿「彭文博」去比 `pengwenbo`，不匹配，
         当场筛掉。表现是「输入 handle 搜得到、输入中文名搜不到」，而接口、fixture、
         服务端用例三处各自看都对。 -->
    <AdaptiveDialog
      :model-value="dialogOpen"
      :title="t('members.addDialog.title')"
      :primary-label="t('members.addDialog.submit')"
      :primary-loading="adding"
      :primary-disabled="!selected.length"
      persistent
      @update:model-value="emit('update:dialogOpen', $event)"
      @primary="emit('submit')"
    >
      <v-autocomplete
        :model-value="selected"
        :search="search"
        autocomplete="off"
        :label="t('members.addDialog.searchLabel')"
        :placeholder="t('members.addDialog.searchPlaceholder')"
        variant="outlined"
        density="comfortable"
        :items="candidates"
        :loading="searching"
        :no-filter="true"
        item-title="handle"
        item-value="handle"
        return-object
        multiple
        chips
        closable-chips
        hide-no-data
        @update:model-value="emit('update:selected', $event)"
        @update:search="emit('update:search', $event)"
      >
        <template #item="{ item, props: itemProps }">
          <v-list-item v-bind="itemProps" :disabled="item.raw.already_admin">
            <template #prepend>
              <UserAvatar :name="item.raw.handle" :avatar="avatarUrl(item.raw.avatar_id)" :size="30" />
            </template>
            <v-list-item-title>{{ item.raw.nickname }}</v-list-item-title>
            <v-list-item-subtitle>{{ item.raw.handle }}</v-list-item-subtitle>
            <template #append>
              <span v-if="item.raw.already_admin" class="chip-neutral">{{ t('members.addDialog.alreadyAdmin') }}</span>
            </template>
          </v-list-item>
        </template>
      </v-autocomplete>
      <div class="t-meta mt-2">{{ t('members.addDialog.hint') }}</div>
    </AdaptiveDialog>

    <!-- 移出要问一句：这个动作当场改的是**谁能看别人的私密反馈**，而且按钮就在一行
         名字旁边，点错了没有任何东西拦着。 -->
    <ConfirmDialog
      :model-value="!!confirmHandle"
      :title="t('members.confirm.title')"
      :confirm-label="t('members.confirm.submit')"
      :loading="!!removing"
      danger
      @update:model-value="emit('update:confirmHandle', null)"
      @confirm="emit('remove')"
    >
      <!-- The body echoes the handle in bold (the person pressing must see who is being
           removed), and English word order differs, so the i18n-t slot is the only
           compliant phrasing. -->
      <i18n-t keypath="members.confirm.body" tag="span">
        <template #handle>
          <strong>{{ confirmHandle }}</strong>
        </template>
      </i18n-t>
    </ConfirmDialog>
  </div>
</template>

<style scoped>
/* 根上还挂着对话框，页面本身（`AdminPage`）要拿到整格高度。 */
.am {
  height: 100%;
}

/* 人数：页头工具槽里的一格元信息。`min-height` 是给「名单还没回来」那一帧留位，
   否则数字到货时工具槽会长一行、页头跟着跳一下。 */
.am__count {
  min-height: var(--lh-12);
  margin-right: 4px;
}

/* 一条横条（错误 / 提示）。**不是 `v-alert`**：那套默认样（大圆角、实色底、整块
   染色）在这一页的表格旁边像另一个产品。这里只留一条：左侧一道 3px 的色标说这是
   哪一类，其余全是这一页自己的底色与描边。 */

/* 名单只有几行，所以这一张卡**贴着内容**，不撑满剩下的高度（反馈那一页相反：它一屏
   十七行，表格自己领滚动）。外面的这一层负责「能缩」—— 名单长起来时它先让位，然后
   表格内部才滚。 */
.am__gridwrap {
  display: flex;
  flex: 0 1 auto;
  min-height: 0;
}

/* 1440 下这一页的容器只有约 1051px，而 `BaseTable` 默认的 1080 表格下限比它宽
   29px —— 最后一列「操作」被裁掉一截，「不可移出」显示成「不可移」。成员表六列
   的定宽合计只有 770，1000 就排得下，剩下的 230 给自适应的「添加信息」那一列。 */
.am :deep(.agrid__table) {
  --agrid-min: 1000px;
}

.am__group {
  background: var(--fill-2);
}

/* 组头那一格的**内边距和行高都不另写**：表壳给 8px 上下 × 12px 左右，行高也由它
   钉成和数据行一样。原来这里写的是 `padding: 0 12px` + `height: 32px` —— 表壳的
   规则压得过它，那两条从来没生效过，留着只会误导。 */
/* **这一格不能写 `display: flex`**：`<td>` 一旦不是 table-cell，`colspan="6"` 就
   作废，浏览器把它当普通块级盒子，宽度退回第一列 —— 组头那条底色于是只剩表格左边
   一小段（1440px 上看着像半条带子）。名字和人数本来就是行内元素，写不写都在一行上。 */
.am__groupcell {
  white-space: nowrap;
}

.am__grouplabel {
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
}

.am__groupcount {
  margin-left: 6px;
  color: var(--faint);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  line-height: var(--lh-12);
}

/* 行的内边距、下边线、悬停底色、圆角都由表壳给
   （`components/base/BaseTable.vue` 的 `.agrid__body :deep(td)` 那一段），
   这里只写「这一格里的字怎么排」。**别在这里重复写内边距**：表壳那条规则按
   `tbody td` 选，压得过这一层的 `.am__cell`，写了也是白写 —— 反而会让下一个人
   以为行高是从这儿来的（第一版就是这样，页面里写着 4px、页面上跑的是 8px）。 */
.am__cell {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.am__cell--actions {
  text-align: right;
}

/* 头像 + 昵称 + handle（+ 可能的 agent 徽章）排成一行。头像是**装饰**（名字就在
   旁边），模板那一层 `aria-hidden` 把它摘出可及性树；这里只管几何。
   头像 `flex: 0 0 auto`：它比文字先该保住的宽度，被裁的应该是字，不是脸。 */
.am__who {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.am__pfp {
  display: inline-flex;
  flex: 0 0 auto;
}

/* 昵称和 handle 同一行、两个字号（13 / 12）。`min-width: 0` 是 flex 子项能缩到比内容
   还窄、从而吃到 `text-overflow` 的前提 —— 不给它，长昵称会把定死的列撑开。被裁掉
   的那半在单元格的 `title` 里。*/
.am__name-main {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.am__name-sub {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.am__dim {
  color: var(--muted);
}

/* 状态列「异常才说话」的那一句。`--warn-ink` 是状态三件套里的**文字件**（§1.5），
   不是拿 `--warn` 记号色写字 —— 那个对比度在字上不够用。 */
.am__warn {
  color: var(--warn-ink);
}

/* 空态那一格想要比一行高一点，所以**真的需要**压过表壳那 8px —— 多写一层 `.am`
   （三个类 `(0,3,0)` > 表壳的 `(0,2,1)`，理由写在 `BaseTable.vue` 顶上那段注释里）。
   这是这个写法在本仓库的实例，照着写就行。 */
.am .am__none {
  padding: 20px 12px;
  color: var(--faint);
  text-align: center;
}

/* 带上 `.v-table`：Vuetify 给列头写的 `text-align: start` 选择器更长，光一个类名压
   不住它，「操作」列头会靠左、底下的按钮靠右。 */
.v-table .am__num {
  text-align: right;
}

/* 窄容器：上下内边距收一档，左右仍是 16，和页头标题同一条左沿。后台页的断点挂在
   内容列上（§3.5），不用视口媒体查询。容器是 `.app-page__column--admin`（名字
   `admin`）—— 指名查询，免得 `.am__grouplabel` 落在更近的 `agrid` 容器上。 */
@container admin (max-width: 719.98px) {
  .am__body {
    padding: 12px 16px 16px;
  }

  /* 卡片里没有「一列宽」这回事，说明性文字（组头那句长 handle 说明）让它折行。 */
  .am__grouplabel {
    white-space: normal;
  }
}
</style>
