// 资料库的文件夹：名字里的 `/` 就是文件夹，没有单独的文件夹清单。一层里有哪些文件夹
// 由后端从记录表里聚出来（`listProjectLibrary`）；这里只管一条路径怎么拆。

/** 最后一层的名字。 */
export function leafOf(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1)
}

/** 所在的文件夹；在最上层时是 `''`。 */
export function folderOf(path: string): string {
  const at = path.lastIndexOf('/')
  return at < 0 ? '' : path.slice(0, at)
}

/** 面包屑：从最上层到 `dir` 的每一层，`合同/2026` → `合同`、`合同/2026`。 */
export function crumbs(dir: string): { path: string; name: string }[] {
  if (!dir) return []
  const parts = dir.split('/')
  return parts.map((name, i) => ({ path: parts.slice(0, i + 1).join('/'), name }))
}
