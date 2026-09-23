/**
 * 本次页面会话里「要过、但被拒」的头像 URL。
 *
 * 为什么需要它：`/avatars/{id}` 对「avatars 表里有行、盘上却没文件」的 id 回 404，
 * 而那个 404 上**没有**任何缓存头 —— `Cache-Control`/`ETag` 只在真图那条路径上发
 * （见 backend/app/api/routes/avatars.py 的 `_stored_avatar_response`），所以浏览器
 * 缓存里没有一条可以复用的响应。`v-img` 卸载重建（切走页面再回来）会造一个新的
 * `<img src=…>`，浏览器就只能再发一次注定失败的请求。dev 的种子迁移只往 avatars
 * 表写了行 1..5、一张图都没落盘，取不到的是多数，一屏成员列表就是一串 404。
 *
 * 为什么是「会话级」而不是顺手塞进 localStorage：这个集合一旦跨会话留下来，素材
 * 后来补上了、用户也刷新了，旧记录还在，本机永远看不到那张图，只能去清站点数据 ——
 * 缓存失效的代价被转嫁给了用户。模块级变量随一次页面加载而生灭，刷新即恢复，代价
 * 只有「本次会话内补的图看不见」这一条，且自动受限。这正是它不能做成长期缓存的
 * 原因，也是它敢不设 TTL 的原因。
 *
 * 只记失败：成功的头像后端给了 `public, max-age=31536000`，浏览器自己不会再问，
 * 再记一层是多余的。反过来，若哪天后端开始给 404 也配 `no-store` 之类的头，这里
 * 的存在只是让本会话少发几次请求，不改变任何语义。
 *
 * 用普通 `Set` 而不是 reactive：它只在组件新建（或 `avatar` 变了）时被读一次，
 * 决定要不要渲染 `v-img`；失败当场的回落由 `v-img` 的 error 插槽接管，不需要触发
 * 重渲染。做成 reactive 反而会让报错的那一帧把 `v-img` 整个拆掉，比留着它画首字母
 * 更跳。
 */
const failedAvatarUrls = new Set<string>()

/** 这个 URL 本次会话里已经失败过 —— 不必再造一个 `<img>` 去问一次。 */
export function isAvatarKnownFailed(url: string): boolean {
  return url !== '' && failedAvatarUrls.has(url)
}

/** 记下一张取不到的头像。空串是空操作：空串是「没有图」，不是「取不到的图」。 */
export function rememberAvatarFailure(url: string): void {
  if (url) failedAvatarUrls.add(url)
}
