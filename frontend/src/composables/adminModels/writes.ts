// 模型那一段的写：新增 / 编辑、删除、停用 / 启用、改档位、改要不要开通。
// 每一处都是「置忙 → 打端点 → 整表重拉 → 报错」，成功后横条说一句，失败落到写失败那条
// 横条上（框里的失败留在框里，见 `onFormSubmit`）。
//
// 这一段自己的那几张表单与确认框的状态跟着它们走；写失败那条横条和「写成了」那句由
// 门面持有（线路、额度那两段也在往上放），这里只往里写。
import type { ModelFormPayload } from '@/components/admin/AdminModelFormDialog.vue'
import type { ModelTier } from '@/lib/adminCredits'
import type { ModelRow } from '@/lib/adminModels'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { type AdminModelsBanner, type AdminModelsReload, message } from './shared'

import { createGatewayModel, deleteGatewayModel, setGatewayModelBlocked, updateGatewayModel } from '@/api'
import { setGatewayModelTier } from '@/api/adminCredits'

export interface AdminModelsWritesHost extends AdminModelsBanner, AdminModelsReload {}

export function useAdminModelsWrites(host: AdminModelsWritesHost) {
  const { t } = useI18n()
  const { writeError, notice, load } = host

  const formOpen = ref(false)
  const formMode = ref<'add' | 'edit'>('add')
  const formSeed = ref<ModelRow | null>(null)
  const formSaving = ref(false)
  const formError = ref<string | null>(null)

  /** 删除 / 停用都要过一道确认：它们当场改的是全平台的路由，按钮又在一行名字旁边。 */
  const deleteTarget = ref<ModelRow | null>(null)
  const deleting = ref(false)
  const blockTarget = ref<ModelRow | null>(null)
  const blocking = ref(false)

  function openAdd() {
    formMode.value = 'add'
    formSeed.value = null
    formError.value = null
    formOpen.value = true
  }

  function openEdit(row: ModelRow) {
    formMode.value = 'edit'
    formSeed.value = row
    formError.value = null
    formOpen.value = true
  }

  async function onFormSubmit(payload: ModelFormPayload) {
    formSaving.value = true
    formError.value = null
    writeError.value = null
    try {
      const { source, ...model } = payload
      if (formMode.value === 'add') {
        // add 模式表单必带 `name` 与第一条线路（空时不放行），这里的 `?? ''` 只是把它们
        // 从可选收成必填，真正的校验仍在服务端。
        await createGatewayModel({
          ...source,
          ...model,
          name: payload.name ?? '',
          upstream_model: source?.upstream_model ?? '',
        })
        notice.value = t('models.notice.added', { name: payload.name ?? '' })
      } else {
        await updateGatewayModel(formSeed.value?.name ?? '', model)
        notice.value = t('models.notice.updated', { name: formSeed.value?.name ?? '' })
      }
      formOpen.value = false
      await load()
    } catch (e) {
      // 失败不关框、原因照原话显示 —— 关掉框等于把刚填的一屏字和「为什么退回」一起丢掉。
      formError.value = message(e, t('models.page.saveFailed'))
    } finally {
      formSaving.value = false
    }
  }

  function askDelete(row: ModelRow) {
    writeError.value = null
    deleteTarget.value = row
  }

  /** 确认框自己关上（取消 / Esc / 点外面）：把目标清掉。原来的写法是「任何一次更新都
   *  清」，而 `v-dialog` 只会发 `false` —— 两句话说的是同一个结果，收在这里一次。 */
  function closeDelete() {
    deleteTarget.value = null
  }

  async function confirmDelete() {
    const row = deleteTarget.value
    if (!row) return
    deleting.value = true
    writeError.value = null
    try {
      await deleteGatewayModel(row.name)
      notice.value = t('models.notice.deleted', { name: row.name })
      deleteTarget.value = null
      await load()
    } catch (e) {
      writeError.value = message(e, t('models.page.deleteFailed'))
      deleteTarget.value = null
    } finally {
      deleting.value = false
    }
  }

  function askBlock(row: ModelRow) {
    writeError.value = null
    blockTarget.value = row
  }

  /** 同上一条：停用 / 启用那个框自己关上。 */
  function closeBlock() {
    blockTarget.value = null
  }

  async function confirmBlock() {
    const row = blockTarget.value
    if (!row) return
    const next = !row.blocked
    blocking.value = true
    writeError.value = null
    try {
      await setGatewayModelBlocked(row.name, next)
      notice.value = next
        ? t('models.notice.blocked', { name: row.name })
        : t('models.notice.unblocked', { name: row.name })
      blockTarget.value = null
      await load()
    } catch (e) {
      writeError.value = message(e, t('models.page.blockFailed'))
      blockTarget.value = null
    } finally {
      blocking.value = false
    }
  }

  /** 改一条模型的档位：方案按档位限定可用的模型。成功后整表重拉，失败落到写失败那条横条。 */
  async function setTier(row: ModelRow, tier: ModelTier) {
    if (row.tier === tier) return
    writeError.value = null
    try {
      await setGatewayModelTier(row.name, tier)
      await load()
    } catch (e) {
      writeError.value = message(e, t('models.table.tierFailed'))
    }
  }

  /** 改一条模型要不要开通才能用：走同一条更新接口，只带 `overseas`。成功后整表重拉，失败落到写失败那条横条。 */
  async function setOverseas(row: ModelRow, overseas: boolean) {
    if (!!row.overseas === overseas) return
    writeError.value = null
    try {
      await updateGatewayModel(row.name, { overseas })
      await load()
    } catch (e) {
      writeError.value = message(e, t('models.table.overseasFailed'))
    }
  }

  return {
    formOpen,
    formMode,
    formSeed,
    formSaving,
    formError,
    openAdd,
    openEdit,
    onFormSubmit,
    deleteTarget,
    deleting,
    askDelete,
    closeDelete,
    confirmDelete,
    blockTarget,
    blocking,
    askBlock,
    closeBlock,
    confirmBlock,
    setTier,
    setOverseas,
  }
}
