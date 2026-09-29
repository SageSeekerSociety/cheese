// 命令面板里的「操作」：此刻登记着的命令（useCommands / defineCommands）。`>` 只看
// 这一类。标了 `palette: false` 的不进来，点不动的也不进来。后登记的在前：这一页、
// 这个房间的事比「新建项目」这种哪都能做的事更可能是现在要找的。
import type { PaletteSource } from './palette/sources'

import { runCommand } from './shortcuts'
import { activeCommands } from '.'

const source: PaletteSource = {
  id: 'commands',
  label: 'navigation.palette.commands',
  order: 50,
  prefix: '>',
  items: (ctx) =>
    [...activeCommands.value]
      .reverse()
      .filter((command) => command.palette !== false && !command.disabled)
      .map((command) => ({
        id: `command:${command.id}`,
        title: command.title,
        icon: command.icon ?? 'mdi-chevron-right',
        shortcut: command.shortcut,
        run: () => runCommand(command, ctx.router),
      })),
}

export default source
