// 整个项目导出的一份 tar 的地址（docs/project-export.md）。
//
// 放在这里而不是 api.ts：api.ts 早就超过了仓库的单文件上限（guards 的 file-size，
// 只许减不许增），别的下载地址还留在那里，这一条另起一个小模块。
import { BASE } from '@/api/http'

/** 后端把整个项目打包成一份 tar：仓库的 Git bundle、资料库文件、各房间能看的产物与
 *  文件、每份文档的 Markdown，以及一份带 SHA-256 的 manifest.json。取的是调用者当前
 *  能看的范围，超大项目要等一会儿才产出。
 *
 *  它和别的下载一样要走 `downloadFile`（`download=true` 后端不看它）：那条通路会带上
 *  本次请求自己的 Authorization，后端只认带项目权限的人类 Bearer token，直链会绕过
 *  这道门。 */
export function projectExportUrl(projectId: string): string {
  return `${BASE}/projects/${encodeURIComponent(projectId)}/export`
}
