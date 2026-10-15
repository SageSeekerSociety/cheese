/**
 * dayjs 的四个插件在应用入口 `src/main.ts` 里注册，插件的类型也随那里的 import 进工程。
 * 只编译改动文件及其引用的工程（CI 快检的那个）里没有 `main.ts`，插件就当作没装，
 * 用到 `fromNow()`、`dayjs.duration()` 的文件被报成类型错误。这里把插件类型放进
 * 每个工程：src 下的 .d.ts 两种工程都收，于是两种工程看到的插件一样多——全量那一套
 * 本来就通过 `main.ts` 看到了，窄的这套此前看不到。
 *
 * 注意这几位只在应用入口注册：`src/demo-main.ts`（文档演示、组件预览站那个入口）没有
 * 注册它们，所以这里只是把类型补齐，不代表任何入口都能调 `fromNow()`。
 */
import 'dayjs/plugin/duration'
import 'dayjs/plugin/relativeTime'
import 'dayjs/plugin/timezone'
import 'dayjs/plugin/utc'
