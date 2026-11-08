import base from "./vite.config"; export default { ...base, server: { ...base.server, hmr: false } }
