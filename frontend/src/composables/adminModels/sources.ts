// 线路（详情抽屉里那一段）的写：加一条、改一条、提前 / 延后、删一条、停用 / 启用。
// 一次线路写成功之后，横条说一句，列表与抽屉都重拉（抽屉里画的是写前的线路）。
//
// 表单、两种确认、提前 / 延后共用一个 `sourceBusy`：同一个模型的线路一次只改一处，
// 连点出来的第二次写只会让人看不懂最后是谁生效。
import type { Ref } from 'vue'
import type { SourceRow } from '@/lib/adminModels'
import type { PriceChange, SourceDraft } from '@/lib/adminSources'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { type AdminModelsBanner, type AdminModelsReload, message } from './shared'

import {
  addGatewaySource,
  deleteGatewaySource,
  reorderGatewaySources,
  setGatewaySourceBlocked,
  updateGatewaySource,
} from '@/api'
import { draftToFields, priceChanges } from '@/lib/adminSources'

export interface AdminModelsSourcesHost extends AdminModelsBanner, AdminModelsReload {
  /** 抽屉开着时写完一条线路就加一，让抽屉重拉。 */
  drawerReload: Ref<number>
}

export function useAdminModelsSources(host: AdminModelsSourcesHost) {
  const { t } = useI18n()
  const { writeError, notice, load, drawerReload } = host

  const sourceFormOpen = ref(false)
  const sourceFormMode = ref<'add' | 'edit'>('add')
  const sourceModel = ref('')
  const sourceSeed = ref<SourceRow | null>(null)
  const sourceSiblings = ref<SourceRow[]>([])
  const sourceSaving = ref(false)
  const sourceError = ref<string | null>(null)
  const sourceBusy = ref(false)
  const sourceDeleteTarget = ref<{ name: string; source: SourceRow } | null>(null)
  const sourceBlockTarget = ref<{ name: string; source: SourceRow } | null>(null)
  /** 改价之前那一问：改了哪几档、改前改后。确认了才真的提交那张表单。 */
  const priceChange = ref<{ name: string; changes: PriceChange[]; draft: SourceDraft } | null>(null)

  /** 一次线路写成功之后：横条说一句，列表与抽屉都重拉（抽屉里画的是写前的线路）。 */
  async function afterSourceWrite(text: string) {
    notice.value = text
    drawerReload.value += 1
    await load()
  }

  function openAddSource(name: string, siblings: SourceRow[]) {
    sourceFormMode.value = 'add'
    sourceModel.value = name
    sourceSeed.value = null
    sourceSiblings.value = siblings
    sourceError.value = null
    sourceFormOpen.value = true
  }

  function openEditSource(name: string, source: SourceRow) {
    sourceFormMode.value = 'edit'
    sourceModel.value = name
    sourceSeed.value = source
    sourceError.value = null
    sourceFormOpen.value = true
  }

  /** 编辑一条线路时改了价：先停在确认框上（改前 → 改后），确认才提交。加线路是定价，不是改价。 */
  function onSourceSubmit(draft: SourceDraft) {
    if (sourceFormMode.value === 'edit' && sourceSeed.value) {
      const changes = priceChanges(sourceSeed.value.prices, draftToFields(draft, 'edit').prices)
      if (changes.length) {
        priceChange.value = { name: sourceModel.value, changes, draft }
        return
      }
    }
    return submitSource(draft)
  }

  function confirmPriceChange() {
    const pending = priceChange.value
    priceChange.value = null
    if (pending) return submitSource(pending.draft)
  }

  function closePriceChange() {
    priceChange.value = null
  }

  async function submitSource(draft: SourceDraft) {
    const name = sourceModel.value
    sourceSaving.value = true
    sourceBusy.value = true
    sourceError.value = null
    writeError.value = null
    try {
      if (sourceFormMode.value === 'add') {
        const fields = draftToFields(draft, 'add')
        await addGatewaySource(name, {
          ...fields,
          upstream_model: fields.upstream_model ?? '',
          order: fields.order ?? 1,
        })
        sourceFormOpen.value = false
        await afterSourceWrite(t('models.source.notice.added', { name }))
      } else {
        await updateGatewaySource(name, sourceSeed.value?.source_id ?? '', draftToFields(draft, 'edit'))
        sourceFormOpen.value = false
        await afterSourceWrite(t('models.source.notice.updated', { name }))
      }
    } catch (e) {
      // 同模型表单：失败不关框、原因照原话显示。
      sourceError.value = message(e, t('models.source.failed'))
    } finally {
      sourceSaving.value = false
      sourceBusy.value = false
    }
  }

  /** 提前 / 延后：这一条的优先级改成 `order`。失败落到页顶那条横条。 */
  async function moveSource(name: string, source: SourceRow, order: number) {
    sourceBusy.value = true
    writeError.value = null
    try {
      await reorderGatewaySources(name, { [source.source_id]: order })
      await afterSourceWrite(t('models.source.notice.reordered', { name }))
    } catch (e) {
      writeError.value = message(e, t('models.source.failed'))
    } finally {
      sourceBusy.value = false
    }
  }

  function askSourceDelete(name: string, source: SourceRow) {
    writeError.value = null
    sourceDeleteTarget.value = { name, source }
  }

  function closeSourceDelete() {
    sourceDeleteTarget.value = null
  }

  async function confirmSourceDelete() {
    const target = sourceDeleteTarget.value
    if (!target) return
    sourceBusy.value = true
    writeError.value = null
    try {
      await deleteGatewaySource(target.name, target.source.source_id)
      sourceDeleteTarget.value = null
      await afterSourceWrite(t('models.source.notice.deleted', { name: target.name }))
    } catch (e) {
      writeError.value = message(e, t('models.source.failed'))
      sourceDeleteTarget.value = null
    } finally {
      sourceBusy.value = false
    }
  }

  function askSourceBlock(name: string, source: SourceRow) {
    writeError.value = null
    sourceBlockTarget.value = { name, source }
  }

  function closeSourceBlock() {
    sourceBlockTarget.value = null
  }

  async function confirmSourceBlock() {
    const target = sourceBlockTarget.value
    if (!target) return
    const next = !target.source.blocked
    sourceBusy.value = true
    writeError.value = null
    try {
      await setGatewaySourceBlocked(target.name, target.source.source_id, next)
      sourceBlockTarget.value = null
      await afterSourceWrite(
        t(next ? 'models.source.notice.blocked' : 'models.source.notice.unblocked', { name: target.name })
      )
    } catch (e) {
      writeError.value = message(e, t('models.source.failed'))
      sourceBlockTarget.value = null
    } finally {
      sourceBusy.value = false
    }
  }

  return {
    sourceFormOpen,
    sourceFormMode,
    sourceModel,
    sourceSeed,
    sourceSiblings,
    sourceSaving,
    sourceError,
    sourceBusy,
    openAddSource,
    openEditSource,
    onSourceSubmit,
    priceChange,
    confirmPriceChange,
    closePriceChange,
    moveSource,
    sourceDeleteTarget,
    askSourceDelete,
    closeSourceDelete,
    confirmSourceDelete,
    sourceBlockTarget,
    askSourceBlock,
    closeSourceBlock,
    confirmSourceBlock,
  }
}
