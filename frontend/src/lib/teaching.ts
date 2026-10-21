// 「AI 队友辅导」在表单上那几格与接口那一份之间的翻译。
//
// 服务端存的是**整份**（`SpaceTeaching`，见 `backend/app/domain/task/protocol.py`）：
// 方式、补充要求、资料、文档。空间和题目两层同时生效，所以题目那一层留空就是只用
// 空间的；写了什么就在空间的基础上加什么。
//
// 空间设置页和发题页、改题页对同一份东西用同一套规矩，放在这里免得几处各写一遍。
import type { SpaceTeaching, TeachingMode } from '@/types'

/** 方式的四种，按表单上的顺序。 */
export const TEACHING_MODES: readonly TeachingMode[] = ['guide', 'example', 'spec', 'free']

/** 表单上那几格。 */
export type TeachingDraft = {
  mode: TeachingMode | null
  systemPrompt: string
  materialIds: number[]
  /** 表单上不改，原样带着：老配置里写过的团队文档不因为换了表单就丢掉。 */
  documentIds: string[]
}

export function emptyTeachingDraft(): TeachingDraft {
  return { mode: null, systemPrompt: '', materialIds: [], documentIds: [] }
}

/** 接口那一份 → 表单那几格。 */
export function draftFromConfig(config?: SpaceTeaching | null): TeachingDraft {
  return {
    mode: config?.mode ?? null,
    systemPrompt: config?.systemPrompt ?? '',
    materialIds: [...(config?.materialIds ?? [])],
    documentIds: [...(config?.documentIds ?? [])],
  }
}

/** 表单那几格 → 交给接口的那一份。空的落成 `null` / `[]`，与 `is_empty` 一个意思。 */
export function buildTeaching(draft: TeachingDraft): SpaceTeaching {
  return {
    mode: draft.mode,
    systemPrompt: draft.systemPrompt.trim() || null,
    materialIds: [...draft.materialIds],
    documentIds: [...draft.documentIds],
  }
}

/** 全空 = 这一层什么都没说。题目那一层据此决定「本题有没有自己的辅导设置」。 */
export function isTeachingBlank(teaching?: SpaceTeaching | null): boolean {
  if (!teaching) return true
  return (
    !teaching.mode &&
    !teaching.systemPrompt?.trim() &&
    (teaching.materialIds?.length ?? 0) === 0 &&
    (teaching.documentIds?.length ?? 0) === 0
  )
}
