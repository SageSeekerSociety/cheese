/**
 * dayjs 的四个插件只在 `src/main.ts` 注册，插件的类型也随那里的 import 进工程。
 * 只编译改动文件及其引用的工程（CI 快检的那个）里没有 `main.ts`，插件就当作没装，
 * 用到 `fromNow()`、`dayjs.duration()` 的文件被报成类型错误。这里把插件类型放进
 * 每个工程：src 下的 .d.ts 两种工程都收，而写的这四个插件确实是全站注册过的。
 */
import 'dayjs/plugin/duration'
import 'dayjs/plugin/relativeTime'
import 'dayjs/plugin/timezone'
import 'dayjs/plugin/utc'
