// 资料库一页的查询条件，以及它怎么写成查询串。单放一处：`api.ts` 要用它，而
// `libraryApi.ts` 反过来要 `api.ts` 的 request，放在那边就成了一个环。

/** 资料库一页的条件：不搜不筛时是 `dir` 那一层（文件夹在前），`flat` / `q` / `kind`
 *  给了就是整个资料库里对得上的文件。翻页用上一页的 `next`。 */
export interface LibraryQuery {
  dir?: string
  /** 整个资料库平铺（`q` / `kind` 给了也是）。 */
  flat?: boolean
  q?: string
  kind?: string
  cursor?: string | null
  limit?: number
}

/** `LibraryQuery` 写成查询串，空的不写。 */
export function libraryParams(query: LibraryQuery): URLSearchParams {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query))
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  return params
}
