<script setup lang="ts">
// 邀请码 —— 看得见、改得动、收得回。由头部那块下拉打开（那颗下拉只对所有者与管理员开）。
//
// 原来是整页 `views/spaces/detail/ManageInviteCodes.vue`。收进弹窗不只是挪个位置：
// 「可用人数与有效期随时可调」在这之前**后端根本没有改码接口**，界面上只能建与看。
// 现在 `PATCH` 与 `DELETE /spaces/{id}/invite-codes/{codeId}` 都在了，这一屏是它们的门面。
//
// 两处刻意的做法：
// - 调整是「把这一行改成这个样子」：可用人数与有效期一起发回去，不是只发动过的那一项。
//   表单本来就预填着当前值，一起发等于「照这个样子生效」，比猜哪一项被人碰过更好懂。
//   有效期留空 = 永不过期，和新建那张表单同一个说法，所以不需要再多一个开关。
// - 撤销要点两下，第二下才是真的。撤销不可逆 —— 码从此对所有人无效、也不再出现在这张
//   表里 —— 一步点到就撤掉的话误触没有回头路。
import type { SpaceInviteCode } from '@/types'

import { computed, ref, watch } from 'vue'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import { SpacesApi } from '@/network/api/spaces'

const props = defineProps<{ spaceId: number }>()
const open = defineModel<boolean>({ required: true })

const codes = ref<SpaceInviteCode[]>([])
const loading = ref(false)
const busy = ref(false)
const copiedId = ref<number | null>(null)

/** 正在被调整的那一行；null = 没有人在调整。 */
const editingId = ref<number | null>(null)
const editMaxUses = ref('')
const editExpiresOn = ref('')

/** 已经点过一下撤销、正在等第二下的那一行。 */
const confirmingId = ref<number | null>(null)

const newOpen = ref(false)
const newMaxUses = ref('50')
const newExpiresOn = ref('')

const usable = computed(() => codes.value.filter((c) => statusOf(c).key === 'usable'))
const active = computed(() => usable.value[0] ?? codes.value[0])

async function refresh() {
  loading.value = true
  try {
    const res = await SpacesApi.listInviteCodes(props.spaceId)
    codes.value = res.data.inviteCodes ?? []
  } catch {
    toast.error('邀请码读不出来')
  } finally {
    loading.value = false
    editingId.value = null
    confirmingId.value = null
  }
}

watch(
  () => [open.value, props.spaceId] as const,
  ([isOpen]) => {
    if (isOpen) refresh()
  },
  { immediate: true }
)

/** 状态是算出来的，不是存的：库里只有次数与期限，两者都能让一张码失效。 */
function statusOf(item: SpaceInviteCode) {
  if (item.expiresAt !== null && item.expiresAt <= Date.now()) {
    return { key: 'expired', label: '已过期', color: 'warning' }
  }
  if (item.maxUses > 0 && item.useCount >= item.maxUses) {
    return { key: 'exhausted', label: '已用尽', color: 'error' }
  }
  return { key: 'usable', label: '可用', color: 'success' }
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
    await SpacesApi.updateInviteCode(props.spaceId, item.id, {
      maxUses,
      // 留空 = 永不过期，所以这里发的是显式的 null（后端把「不发」读作「别动这一项」）。
      expiresAt: editExpiresOn.value ? dayjs(editExpiresOn.value).endOf('day').valueOf() : null,
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
    await SpacesApi.revokeInviteCode(props.spaceId, item.id)
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
    await SpacesApi.createInviteCode(props.spaceId, {
      maxUses,
      // `type="date"` 只给到日，取当天的最后一刻 —— 否则选「今天」当场就是过期的。
      ...(newExpiresOn.value ? { expiresAt: dayjs(newExpiresOn.value).endOf('day').valueOf() } : {}),
    })
    newOpen.value = false
    newExpiresOn.value = ''
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
  <v-dialog v-model="open" max-width="720" scrollable>
    <v-card rounded="lg">
      <v-card-title class="d-flex align-center ga-3">
        <span>邀请码</span>
        <v-spacer />
        <v-btn
          color="primary"
          variant="flat"
          size="small"
          prepend-icon="mdi-plus"
          @click="(newOpen = true), (editingId = null), (confirmingId = null)"
        >
          新建
        </v-btn>
        <v-btn icon="mdi-close" variant="text" size="small" @click="open = false" />
      </v-card-title>

      <v-card-subtitle class="pb-2">
        拿到码的人加入之后就是这块板的成员。可用人数与有效期在这里随时可改，改完立刻生效；
        撤销之后那个码对所有人都失效。
      </v-card-subtitle>

      <v-divider />

      <v-card-text>
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
              {{ item.expiresAt ? `有效期至 ${formatDate(item.expiresAt)}` : '永不过期' }}
            </v-list-item-subtitle>

            <!-- 调整：就地改，不跳页。这一行预填着当前值，保存就是「照这个样子生效」。 -->
            <div v-if="editingId === item.id" class="form-row mt-2">
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

            <template #append>
              <div class="codes__actions">
                <v-btn
                  v-if="editingId !== item.id"
                  size="small"
                  variant="text"
                  :disabled="busy"
                  @click="startEdit(item)"
                >
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

        <v-sheet v-else class="pa-6 text-center">
          <p class="text-medium-emphasis mb-3">这块板现在没有可用的码。</p>
        </v-sheet>
      </v-card-text>

      <v-divider />
      <v-card-actions>
        <span class="foot">
          当前可用码：{{ active ? active.code : '暂无' }}
          <template v-if="active">（{{ active.useCount }} / {{ active.maxUses }} 人已用）</template>
        </span>
        <v-spacer />
        <v-btn variant="text" @click="open = false">关闭</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped lang="scss">
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

.foot {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
}
</style>
