<script setup lang="ts">
// 邀请码：看得见、改得动、收得回。只对所有者与管理员开放（接口也这么把关）。
//
// 两处刻意的做法：
// - 调整是「把这一行改成这个样子」：可用人数、有效期、说明一起发回去，不是只发动过的
//   那一项。表单本来就预填着当前值，一起发等于「照这个样子生效」，比猜哪一项被人碰过
//   更好懂。有效期留空 = 永不过期，和新建那张表单同一个说法，所以不需要再多一个开关。
//   说明同理：后端把「不发这一项」读作「别动它」、把 null 读作「清掉」，所以表单必须
//   把自己那份值原样发出去 —— 只发改过的那一项的话，清空说明就等于没清。
// - 撤销要点两下，第二下才是真的。撤销不可逆 —— 码从此对所有人无效、也不再出现在这张
//   表里 —— 一步点到就撤掉的话误触没有回头路。
//
// 「当前使用中的码」不是「列表里第一张」：一张用尽或过期的码就躺在列表第一格上，
// 拿它当当前码，人照着发给下一个人的时候才发现发不出去。判据在 `model.ts`
// （`currentInviteCode`）。
import type { SpaceInviteCode } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import { currentInviteCode, inviteCodeStatus } from '../model'

import PageHeader from '@/components/common/PageHeader.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { SpacesApi } from '@/network/api/spaces'

const route = useRoute()
const spaceId = Number(route.params.spaceId)

const codes = ref<SpaceInviteCode[]>([])
const loading = ref(false)
const busy = ref(false)
const copiedId = ref<number | null>(null)

/** 正在被调整的那一行；null = 没有人在调整。 */
const editingId = ref<number | null>(null)
const editMaxUses = ref('')
const editExpiresOn = ref('')
const editNote = ref('')

/** 已经点过一下撤销、正在等第二下的那一行。 */
const confirmingId = ref<number | null>(null)

const newOpen = ref(false)
const newMaxUses = ref('50')
const newExpiresOn = ref('')
const newNote = ref('')

/** 「当前使用中的码」。没有就是 `null` —— 不拿一张用尽或过期的码顶上。 */
const active = computed(() => currentInviteCode(codes.value))

/** 建码人的显示名。`created_by` 可空（没有 actor 的调用者建的码留着空），
 *  空的那一栏写「未知」，不写空串 —— 空串看起来像「这个人没有名字」。 */
function makerName(item: SpaceInviteCode) {
  const maker = item.createdBy
  if (!maker) return '未知'
  return maker.nickname || maker.username
}

/** 表单里那份说明，发出去之前的归一：全是空白 = 没有说明（后端也这么读）。 */
function noteFrom(value: string): string | null {
  return value.trim() || null
}

async function refresh() {
  loading.value = true
  try {
    const res = await SpacesApi.listInviteCodes(spaceId)
    codes.value = res.data.inviteCodes ?? []
  } catch {
    toast.error('邀请码读不出来')
  } finally {
    loading.value = false
    editingId.value = null
    confirmingId.value = null
  }
}

onMounted(refresh)

/** 状态是算出来的，不是存的：库里只有次数与期限，两者都能让一张码失效。
 *  判据本身在 `model.ts`（`inviteCodeStatus`）。 */
function statusOf(item: SpaceInviteCode) {
  return inviteCodeStatus(item)
}

function formatDate(ms: number) {
  return dayjs(ms).format('YYYY-MM-DD')
}

function asDateInput(ms: number | null) {
  return ms === null ? '' : dayjs(ms).format('YYYY-MM-DD')
}

async function copyCode(item: SpaceInviteCode) {
  try {
    await navigator.clipboard.writeText(item.code)
    copiedId.value = item.id
    setTimeout(() => (copiedId.value = null), 1600)
  } catch {
    // 剪贴板被拒（非安全上下文 / 没授权）——码还在屏幕上，手工选中复制即可。
  }
}

function startEdit(item: SpaceInviteCode) {
  confirmingId.value = null
  editingId.value = item.id
  editMaxUses.value = String(item.maxUses || 1)
  editExpiresOn.value = asDateInput(item.expiresAt)
  // 预填当前说明：保存是「照这个样子生效」，所以这一格必须带着它原来的值 ——
  // 留空保存等于把说明清掉（后端把那读作显式的 null），那是另一件事。
  editNote.value = item.note ?? ''
}

async function saveEdit(item: SpaceInviteCode) {
  if (busy.value) return
  const maxUses = Number(editMaxUses.value)
  if (!Number.isInteger(maxUses) || maxUses < 1) {
    toast.error('可用人数要是正整数')
    return
  }
  // 后端也挡这一条（会 400），先在本地说明白：把人数调到比已经用掉的还少，码当场就废了。
  if (maxUses < item.useCount) {
    toast.error(`可用人数不能少于已经用掉的 ${item.useCount} 人`)
    return
  }
  busy.value = true
  try {
    await SpacesApi.updateInviteCode(spaceId, item.id, {
      maxUses,
      // 留空 = 永不过期，所以这里发的是显式的 null（后端把「不发」读作「别动这一项」）。
      expiresAt: editExpiresOn.value ? dayjs(editExpiresOn.value).endOf('day').valueOf() : null,
      // 说明同理：表单带着当前值，所以发出去的就是「这一行现在的说明」；清空 = 清掉。
      note: noteFrom(editNote.value),
    })
    await refresh()
    toast.success('已更新')
  } catch {
    toast.error('更新失败')
  } finally {
    busy.value = false
  }
}

async function revoke(item: SpaceInviteCode) {
  if (busy.value) return
  if (confirmingId.value !== item.id) {
    confirmingId.value = item.id
    editingId.value = null
    return
  }
  busy.value = true
  try {
    await SpacesApi.revokeInviteCode(spaceId, item.id)
    await refresh()
    toast.success('已撤销，这个码从此加入不了')
  } catch {
    toast.error('撤销失败')
  } finally {
    busy.value = false
  }
}

async function submitCreate() {
  if (busy.value) return
  const maxUses = Number(newMaxUses.value)
  if (!Number.isInteger(maxUses) || maxUses < 1) {
    toast.error('可用人数要是正整数')
    return
  }
  busy.value = true
  try {
    await SpacesApi.createInviteCode(spaceId, {
      maxUses,
      // `type="date"` 只给到日，取当天的最后一刻 —— 否则选「今天」当场就是过期的。
      ...(newExpiresOn.value ? { expiresAt: dayjs(newExpiresOn.value).endOf('day').valueOf() } : {}),
      note: noteFrom(newNote.value),
    })
    newOpen.value = false
    newExpiresOn.value = ''
    newNote.value = ''
    await refresh()
    toast.success('已生成')
  } catch {
    toast.error('生成失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageHeader title="邀请码" show-on-mobile>
    <template #actions>
      <v-btn
        color="primary"
        variant="flat"
        prepend-icon="mdi-plus"
        @click="(newOpen = true), (editingId = null), (confirmingId = null)"
      >
        新建
      </v-btn>
    </template>
  </PageHeader>
  <div class="invite-codes">
    <div>
      <!-- 「当前使用中的码」单列在这里，不和列表第一格混为一谈：用尽或过期的码就躺在
             列表第一格上，照它发出去是发不出去的。 -->
      <!-- 一张码都没有时只说一次「暂无邀请码」，这一块不出现。 -->
      <v-sheet v-if="codes.length > 0" variant="tonal" rounded="lg" class="current pa-3 mb-4">
        <div class="current__label">当前使用中的码</div>
        <div v-if="active" class="current__body">
          <code class="current__code">{{ active.code }}</code>
          <v-btn
            :icon="copiedId === active.id ? 'mdi-check' : 'mdi-content-copy'"
            size="small"
            variant="text"
            title="复制"
            @click="copyCode(active)"
          />
          <span class="current__meta">
            {{ active.useCount }} / {{ active.maxUses > 0 ? active.maxUses : '不限' }} 人已用
          </span>
        </div>
        <div v-else class="current__none">暂无可用的码</div>
      </v-sheet>

      <v-sheet v-if="newOpen" variant="tonal" color="primary" rounded="lg" class="pa-4 mb-4">
        <div class="form-title">新建一个码</div>
        <div class="form-row">
          <v-text-field
            v-model="newMaxUses"
            autocomplete="off"
            type="number"
            min="1"
            density="compact"
            variant="outlined"
            hide-details
            label="可用人数"
            style="max-width: 160px"
          />
          <v-text-field
            v-model="newExpiresOn"
            autocomplete="off"
            type="date"
            density="compact"
            variant="outlined"
            hide-details
            label="有效期至（不填 = 永不过期）"
            style="max-width: 240px"
          />
          <v-spacer />
          <v-btn variant="text" size="small" :disabled="busy" @click="newOpen = false">取消</v-btn>
          <v-btn color="primary" variant="flat" size="small" :loading="busy" @click="submitCreate"> 生成 </v-btn>
        </div>
        <div class="form-row mt-3">
          <v-text-field
            v-model="newNote"
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            label="说明（这张码给谁 / 干什么用，可不填）"
          />
        </div>
      </v-sheet>

      <div v-if="loading" class="pa-4 text-center">
        <v-progress-circular indeterminate color="primary" />
      </div>

      <v-list v-else-if="codes.length > 0" rounded="lg" class="codes">
        <v-list-item v-for="item in codes" :key="item.id" class="codes__row">
          <template #prepend>
            <v-avatar color="primary-lighten-5" size="38" class="me-3">
              <v-icon color="primary" size="18">mdi-ticket-confirmation-outline</v-icon>
            </v-avatar>
          </template>

          <v-list-item-title class="d-flex flex-wrap align-center ga-2">
            <span class="codes__text">{{ item.code }}</span>
            <v-btn
              :icon="copiedId === item.id ? 'mdi-check' : 'mdi-content-copy'"
              size="small"
              variant="text"
              title="复制"
              @click="copyCode(item)"
            />
            <v-chip :color="statusOf(item).color" size="small" variant="tonal">
              {{ statusOf(item).label }}
            </v-chip>
          </v-list-item-title>

          <v-list-item-subtitle>
            {{ item.useCount }} / {{ item.maxUses > 0 ? item.maxUses : '不限' }} 人已用 ·
            {{ item.expiresAt ? `有效期至 ${formatDate(item.expiresAt)}` : '永不过期' }} · 建码人
            <UserRef v-if="item.createdBy" :handle="item.createdBy.username" :name="makerName(item)" />
            <template v-else>{{ makerName(item) }}</template>
          </v-list-item-subtitle>

          <!-- 说明。没写就写「没写说明」而不是留白：留白读起来像这一格坏了，
                 而「谁都没写过」是一条真话（每一张在这一格存在之前建的码都是）。 -->
          <div class="codes__note">
            <span class="codes__note-tag">说明</span>
            <span v-if="item.note" class="codes__note-text">{{ item.note }}</span>
            <span v-else class="codes__note-text codes__note-text--none">没写说明</span>
          </div>

          <!-- 调整：就地改，不跳页。这一行预填着当前值，保存就是「照这个样子生效」。 -->
          <div v-if="editingId === item.id" class="mt-2">
            <div class="form-row">
              <v-text-field
                v-model="editMaxUses"
                autocomplete="off"
                type="number"
                min="1"
                density="compact"
                variant="outlined"
                hide-details
                label="可用人数"
                style="max-width: 150px"
              />
              <v-text-field
                v-model="editExpiresOn"
                autocomplete="off"
                type="date"
                density="compact"
                variant="outlined"
                hide-details
                label="有效期至（留空 = 永不过期）"
                style="max-width: 240px"
              />
              <v-spacer />
              <v-btn variant="text" size="small" :disabled="busy" @click="editingId = null"> 取消 </v-btn>
              <v-btn color="primary" variant="flat" size="small" :loading="busy" @click="saveEdit(item)"> 保存 </v-btn>
            </div>
            <div class="form-row mt-3">
              <v-text-field
                v-model="editNote"
                autocomplete="off"
                density="compact"
                variant="outlined"
                hide-details
                label="说明（留空 = 清掉）"
              />
            </div>
          </div>

          <template #append>
            <div class="codes__actions">
              <v-btn v-if="editingId !== item.id" size="small" variant="text" :disabled="busy" @click="startEdit(item)">
                调整
              </v-btn>
              <v-btn
                size="small"
                :variant="confirmingId === item.id ? 'flat' : 'text'"
                :color="confirmingId === item.id ? 'error' : undefined"
                :disabled="busy"
                @click="revoke(item)"
              >
                {{ confirmingId === item.id ? '确认撤销' : '撤销' }}
              </v-btn>
            </div>
          </template>
        </v-list-item>
      </v-list>

      <p v-else class="text-medium-emphasis">暂无邀请码</p>
    </div>
  </div>
</template>

<style scoped lang="scss">
.invite-codes {
  max-width: 760px;
  padding: 16px;
}

.codes {
  background: transparent;
}

.codes__row + .codes__row {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.codes__text {
  font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 1rem;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.codes__actions {
  display: flex;
  gap: 4px;
  align-items: center;
}

.codes__note {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin-top: 2px;
}

.codes__note-tag {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.72rem;
}

.codes__note-text {
  color: rgba(var(--v-theme-on-surface), 0.75);
  font-size: 0.78rem;
}

.codes__note-text--none {
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.current {
  background: rgba(var(--v-theme-primary), 0.06);
}

.current__label {
  margin-bottom: 6px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.74rem;
}

.current__body {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.current__code {
  font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.current__meta {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.78rem;
}

.current__none {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
}

.form-title {
  margin-bottom: 10px;
  font-size: 0.85rem;
  font-weight: 600;
}

.form-row {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
</style>
