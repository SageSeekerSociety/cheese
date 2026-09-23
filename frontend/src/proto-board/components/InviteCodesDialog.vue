<script setup lang="ts">
// 邀请码管理。要求 3 说「放在这个下拉窗口」—— 所以入口是头部那块下拉里的
// 「邀请码」一项，本体是这个弹窗：下拉里塞得下一行摘要，塞不下两个输入框和
// 一排按钮，硬塞进去只会做成一个点不开的列表。
//
// 这一屏要立的是「随时可调」：可用人数和有效期都**就地可改**，改完立刻生效。
// 真平台上这件事今天做不到 —— 建码接口有（`POST /spaces/{id}/invite-codes`），
// 改码的接口没有，所以下面每个输入框背后都是一条还不存在的 PATCH。
import { computed, ref } from 'vue'

import { type InviteCode, PEOPLE } from '../fixtures'
import { addCode, codes, me, revokeCode, updateCode } from '../store'

const open = ref(false)

/** 暴露的是**方法**不是那个 ref：父组件拿到 `show()` 才叫得动；拿到一个 Ref 会在
 *  调用时炸「not callable」。 */
function show() {
  open.value = true
}
defineExpose({ show })

const editing = ref<string | null>(null)
const draftUses = ref<number | null>(null)
const draftUnlimited = ref(false)
const draftExpiry = ref<string | null>(null)
const draftNoExpiry = ref(false)
const draftNote = ref('')

const creating = ref(false)

function startEdit(row: InviteCode) {
  editing.value = row.code
  draftUses.value = row.maxUses
  draftUnlimited.value = row.maxUses === null
  draftExpiry.value = row.expiresAt ? row.expiresAt.slice(0, 10) : null
  draftNoExpiry.value = row.expiresAt === null
}

function saveEdit(code: string) {
  updateCode(code, {
    maxUses: draftUnlimited.value ? null : Math.max(0, draftUses.value ?? 0),
    expiresAt:
      draftNoExpiry.value || !draftExpiry.value ? null : new Date(`${draftExpiry.value}T23:59:00`).toISOString(),
  })
  editing.value = null
}

function saveNew() {
  addCode({
    maxUses: draftUnlimited.value ? null : Math.max(1, draftUses.value ?? 10),
    expiresAt:
      draftNoExpiry.value || !draftExpiry.value ? null : new Date(`${draftExpiry.value}T23:59:00`).toISOString(),
    note: draftNote.value.trim() || '未命名',
  })
  creating.value = false
  draftNote.value = ''
}

function openCreate() {
  creating.value = true
  draftUses.value = 20
  draftUnlimited.value = false
  const d = new Date(Date.now() + 14 * 86_400_000)
  draftExpiry.value = d.toISOString().slice(0, 10)
  draftNoExpiry.value = false
  draftNote.value = ''
}

function expiryText(row: InviteCode): string {
  if (row.revoked) return '已撤销'
  if (!row.expiresAt) return '永不过期'
  const ms = new Date(row.expiresAt).getTime() - Date.now()
  const days = Math.ceil(ms / 86_400_000)
  if (days < 0) return '已过期'
  return `${days} 天后过期`
}

function usesText(row: InviteCode): string {
  return row.maxUses === null ? `${row.useCount} / 不限` : `${row.useCount} / ${row.maxUses}`
}

const activeCode = computed(() => codes.value.find((c) => !c.revoked))

async function copy(code: string) {
  try {
    await navigator.clipboard.writeText(code)
    copied.value = code
    setTimeout(() => (copied.value = null), 1400)
  } catch {
    copied.value = null
  }
}
const copied = ref<string | null>(null)

const ownerName = (handle: string) => PEOPLE[handle]?.name ?? handle
</script>

<template>
  <v-dialog v-model="open" max-width="760" scrollable>
    <v-card rounded="lg">
      <v-card-title class="d-flex align-center ga-3 pa-5 pb-2">
        <v-icon icon="mdi-ticket-confirmation-outline" />
        <span class="text-body-1 font-weight-bold">邀请码</span>
        <v-spacer />
        <v-btn variant="text" icon="mdi-close" size="small" @click="open = false" />
      </v-card-title>

      <v-card-subtitle class="px-5 pb-4 text-body-2">
        邀请码决定谁能进这块板。可用人数与有效期随时可调，改完立刻生效。
      </v-card-subtitle>

      <v-divider />

      <v-card-text class="pa-5">
        <div v-if="activeCode" class="current">
          <div class="current__head">
            <span class="current__label">当前使用中的码</span>
            <span class="current__meta">{{ usesText(activeCode) }} · {{ expiryText(activeCode) }}</span>
          </div>
          <div class="current__row">
            <code class="current__code">{{ activeCode.code }}</code>
            <v-btn
              size="small"
              variant="tonal"
              :prepend-icon="copied === activeCode.code ? 'mdi-check' : 'mdi-content-copy'"
              @click="copy(activeCode.code)"
            >
              {{ copied === activeCode.code ? '已复制' : '复制' }}
            </v-btn>
            <v-spacer />
            <v-btn size="small" variant="text" prepend-icon="mdi-pencil" @click="startEdit(activeCode)">调整</v-btn>
          </div>

          <!-- 就地编辑：这是这一屏的主角。 -->
          <div v-if="editing === activeCode.code" class="edit">
            <div class="edit__field">
              <span class="edit__label">可用人数</span>
              <div class="edit__control">
                <v-text-field
                  v-model.number="draftUses"
                  type="number"
                  density="compact"
                  variant="outlined"
                  hide-details
                  :disabled="draftUnlimited"
                  style="max-width: 110px"
                />
                <v-checkbox v-model="draftUnlimited" label="不限" density="compact" hide-details />
              </div>
            </div>
            <div class="edit__field">
              <span class="edit__label">有效期至</span>
              <div class="edit__control">
                <v-text-field
                  v-model="draftExpiry"
                  type="date"
                  density="compact"
                  variant="outlined"
                  hide-details
                  :disabled="draftNoExpiry"
                  style="max-width: 180px"
                />
                <v-checkbox v-model="draftNoExpiry" label="永不过期" density="compact" hide-details />
              </div>
            </div>
            <div class="edit__foot">
              <span
                class="edit__warn"
                :class="{ 'edit__warn--bad': !draftUnlimited && (draftUses ?? 0) < activeCode.useCount }"
              >
                {{
                  !draftUnlimited && (draftUses ?? 0) < activeCode.useCount
                    ? `已用掉 ${activeCode.useCount} 个，限制不能低于它`
                    : `改完立即生效：新的人数上限是 ${draftUnlimited ? '不限' : draftUses}`
                }}
              </span>
              <v-spacer />
              <v-btn variant="text" size="small" @click="editing = null">取消</v-btn>
              <v-btn
                color="primary"
                variant="flat"
                size="small"
                :disabled="!draftUnlimited && (draftUses ?? 0) < activeCode.useCount"
                @click="saveEdit(activeCode.code)"
              >
                保存
              </v-btn>
            </div>
          </div>
        </div>

        <div class="list__head">
          <span>全部邀请码</span>
          <v-spacer />
          <v-btn size="small" variant="tonal" prepend-icon="mdi-plus" @click="openCreate">新建</v-btn>
        </div>

        <div v-if="creating" class="edit edit--new">
          <div class="edit__field">
            <span class="edit__label">说明</span>
            <v-text-field
              v-model="draftNote"
              autocomplete="off"
              density="compact"
              variant="outlined"
              hide-details
              placeholder="比如「十月这批同学」"
            />
          </div>
          <div class="edit__field">
            <span class="edit__label">可用人数</span>
            <div class="edit__control">
              <v-text-field
                v-model.number="draftUses"
                type="number"
                density="compact"
                variant="outlined"
                hide-details
                :disabled="draftUnlimited"
                style="max-width: 110px"
              />
              <v-checkbox v-model="draftUnlimited" label="不限" density="compact" hide-details />
            </div>
          </div>
          <div class="edit__field">
            <span class="edit__label">有效期至</span>
            <div class="edit__control">
              <v-text-field
                v-model="draftExpiry"
                type="date"
                density="compact"
                variant="outlined"
                hide-details
                :disabled="draftNoExpiry"
                style="max-width: 180px"
              />
              <v-checkbox v-model="draftNoExpiry" label="永不过期" density="compact" hide-details />
            </div>
          </div>
          <div class="edit__foot">
            <v-spacer />
            <v-btn variant="text" size="small" @click="creating = false">取消</v-btn>
            <v-btn color="primary" variant="flat" size="small" @click="saveNew">生成</v-btn>
          </div>
        </div>

        <ul class="list">
          <li v-for="row in codes" :key="row.code" class="list__row" :class="{ 'list__row--dead': row.revoked }">
            <div class="list__main">
              <code class="list__code">{{ row.code }}</code>
              <span class="list__note">{{ row.note }}</span>
            </div>
            <div class="list__stats">
              <span
                ><b>{{ usesText(row) }}</b> 人</span
              >
              <span :class="{ 'is-warn': expiryText(row).startsWith('已') }">{{ expiryText(row) }}</span>
              <span class="list__by">{{ ownerName(row.createdBy.handle) }} 建</span>
            </div>
            <div class="list__actions">
              <v-btn
                size="x-small"
                variant="text"
                icon="mdi-content-copy"
                :disabled="row.revoked"
                @click="copy(row.code)"
              />
              <v-btn
                size="x-small"
                variant="text"
                icon="mdi-pencil"
                :disabled="row.revoked || editing === row.code"
                @click="startEdit(row)"
              />
              <v-btn
                size="x-small"
                variant="text"
                icon="mdi-cancel"
                :disabled="row.revoked"
                @click="revokeCode(row.code)"
              />
            </div>
          </li>
        </ul>

        <p class="foot">
          当前身份：{{ me.name }}（只有所有者与管理员看得到这一屏）。人满或过期后，旧码自动失效，不必手动撤。
        </p>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>

<style scoped lang="scss">
.current {
  padding: 14px;
  margin-bottom: 20px;
  background: rgba(var(--v-theme-on-surface), 0.035);
  border-radius: 12px;
}

.current__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 10px;
}

.current__label {
  font-size: 0.78rem;
  font-weight: 600;
}

.current__meta {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
}

.current__row {
  display: flex;
  gap: 10px;
  align-items: center;
}

.current__code,
.list__code {
  padding: 4px 10px;
  font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 0.95rem;
  letter-spacing: 0.06em;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 8px;
}

.edit {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
  padding: 14px;
  margin-top: 14px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: var(--radius-md);
}

.edit--new {
  margin: 0 0 16px;
  background: rgba(var(--v-theme-on-surface), 0.02);
}

.edit__field {
  display: flex;
  gap: 12px;
  align-items: center;
}

.edit__label {
  flex: 0 0 68px;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 0.8rem;
}

.edit__control {
  display: flex;
  gap: 16px;
  align-items: center;
}

.edit__foot {
  display: flex;
  gap: 8px;
  align-items: center;
}

.edit__warn {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
}

.edit__warn--bad {
  color: rgb(var(--v-theme-error));
}

.list__head {
  display: flex;
  align-items: center;
  margin-bottom: 10px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
}

.list {
  padding: 0;
  margin: 0;
  list-style: none;
}

.list__row {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.list__row--dead {
  opacity: 0.45;
}

.list__main {
  display: flex;
  flex: 1 1 45%;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.list__code {
  font-size: 0.82rem;
  letter-spacing: 0.04em;
}

.list__note {
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.list__stats {
  display: flex;
  flex: 1 1 45%;
  gap: 14px;
  justify-content: flex-end;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.76rem;
}

.list__stats b {
  color: rgba(var(--v-theme-on-surface), 0.92);
}

.list__stats .is-warn {
  color: rgb(var(--v-theme-warning));
}

.list__by {
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.list__actions {
  display: flex;
  gap: 2px;
}

.foot {
  margin: 18px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.74rem;
  line-height: 1.6;
}
</style>
