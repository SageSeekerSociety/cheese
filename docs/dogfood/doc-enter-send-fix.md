# 修复回车误触发送

## 目标
修复前端 bug：用输入法打字时按回车"上屏"（如敲 `aaa` 回车变英文），消息被直接发送。

## 根因
这是浏览器差异导致的知名坑：
- **Chrome** 在输入法组合期间的回车按键会带"组合中"标记（`isComposing` / `keyCode 229`），第一版修复靠它判断，Chrome 下有效。
- **Safari** 的事件顺序相反：先发"组合结束"（compositionend），**再**把那下回车当成普通回车发出来——到代码手里时已经不带任何输入法标记，看起来和"想发送"的回车一模一样，于是被误当成发送。张衡复现的就是这种情况。

## 修复方案（两处输入条：`WorkspaceView.vue`、`ChatPanel.vue`）
三道防线叠加：
1. 组合中标记（`isComposing` / `keyCode 229`）——覆盖 Chrome 等。
2. **自己监听输入法开始/结束事件**（compositionstart/end），组合期间的回车一律不发送。
3. **组合刚结束 100ms 内的回车也吞掉**——Safari 那下"上屏回车"和组合结束几乎同时到达（同一次物理按键，间隔 <10ms），而人有意再按一次回车发送至少间隔 100ms 以上，不会误伤。

另外保留：回车必须来自持有焦点的输入框本身；@-补全菜单打开时回车选中第一项不变。

## 进展
- ✅ 两处 composer 已按上述方案重写，`vue-tsc` 通过，前端测试 8/8 通过
- ⏳ 待 @张衡 再次验证（重点：拼音下敲 `aaa` + 回车，应只上屏不发送）

## 参考
- Square 工程博客：Understanding Composition Browser Events（Safari 事件顺序问题及 defer 方案）
- ProseMirror #880：Safari IME 事件处理（compositionJustEnded 标志方案）
