// 「给 AI 队友的指导」在表单上那六格与接口那一份之间的翻译（#944）。
//
// 服务端存的是**整份**（`SpaceTeaching`，见 `backend/app/domain/task/teaching.py`）：
// 写上就整份替换、不深合，留空的那一层就是「没说」，让下一级说话。表单上人敲的是
// 字符串（逗号分隔的清单、可能空着的周次），所以进出各一道转换。
//
// 这两处页面对同一份东西用同一套规矩：空间设置的「给 AI 队友的指导」一栏写的就是
// 空间那层的默认，发题页写的是题目那层的覆盖。放在这里免得两处各写一遍、慢慢走样。
import type { SpaceTeaching } from '@/types'

/** 表单上那六格：存的是人敲的原文，解析交给 `buildTeaching`。 */
export type TeachingDraft = {
  systemPrompt: string
  currentWeek: string
  allowedTopics: string
  avoidInCode: string
  materialIds: string
  knowledgeIds: string
}

export function emptyTeachingDraft(): TeachingDraft {
  return {
    systemPrompt: '',
    currentWeek: '',
    allowedTopics: '',
    avoidInCode: '',
    materialIds: '',
    knowledgeIds: '',
  }
}

/** 接口那一份 → 表单六格。新会话、换了那道题、保存后读回来，都照它重填一遍。 */
export function draftFromConfig(config?: SpaceTeaching | null): TeachingDraft {
  return {
    systemPrompt: config?.systemPrompt ?? '',
    currentWeek: config?.currentWeek == null ? '' : String(config.currentWeek),
    allowedTopics: (config?.allowedTopics ?? []).join(', '),
    avoidInCode: (config?.avoidInCode ?? []).join(', '),
    materialIds: (config?.materialIds ?? []).join(', '),
    knowledgeIds: (config?.knowledgeIds ?? []).join(', '),
  }
}

/** 逗号分隔（中英文逗号都认）→ 去掉空项。 */
export function splitList(value: string): string[] {
  return value
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

/** 同上，但要的是正整数 id（编号）。不是数字的、非正的都丢掉。 */
export function splitIds(value: string): number[] {
  return splitList(value)
    .map(Number)
    .filter((id) => Number.isInteger(id) && id > 0)
}

/** 表单六格 → 交给接口的那一份。空的那几格落成 `null` / `[]`，与 `is_empty` 一个意思。 */
export function buildTeaching(draft: TeachingDraft): SpaceTeaching {
  const week = draft.currentWeek.trim()
  return {
    systemPrompt: draft.systemPrompt.trim() || null,
    currentWeek: week === '' ? null : Number(week),
    allowedTopics: splitList(draft.allowedTopics),
    avoidInCode: splitList(draft.avoidInCode),
    materialIds: splitIds(draft.materialIds),
    knowledgeIds: splitIds(draft.knowledgeIds),
  }
}

/**
 * 六格全空 = 这一层「没说」。
 *
 * 发题页据此决定要不要把 `teaching` 带上：不带就是让空间（或项目集）的默认生效，
 * 而不是写一份空的把下面那层盖住。
 */
export function isTeachingBlank(teaching?: SpaceTeaching | null): boolean {
  if (!teaching) return true
  return (
    !teaching.systemPrompt &&
    teaching.currentWeek == null &&
    (teaching.allowedTopics?.length ?? 0) === 0 &&
    (teaching.avoidInCode?.length ?? 0) === 0 &&
    (teaching.materialIds?.length ?? 0) === 0 &&
    (teaching.knowledgeIds?.length ?? 0) === 0
  )
}
