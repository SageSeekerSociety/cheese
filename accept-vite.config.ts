// 临时验收入口专用，不进仓库：同一份配置，只关掉热更新长连接（经公网预览连不上时 vite 会整页重载，丢掉页面状态）。
import base from './vite.config'
export default { ...base, server: { ...base.server, hmr: false } }
