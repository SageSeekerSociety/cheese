# 芝士Opus 四人终极策略

这份目录包含主策略和逐卡拉表运行数据，目标模式为 `act3autochess@V3_1` 四人终极。

## 文件

- `botOpus2.js`：旧版接口的动态选盟约、买卡、刷新、冻结、升本、卖卡和阵容评分。
- `botOpusPolicy.js`：新版 `createBotPrepSteps(actions, policy)` 的采购适配层；把阵容目标、精锐合成、逐卡拉表和升本门槛接入 `core`、`target`、`searchTargets`、`buyBonus`、`canLevel`。
- `botOpusCarry.js`：逐卡拉表的 `mid`、`late`、`boss` 数据。`mid`/`late` 是 R8/R13 边际清怪率，`boss` 是精锐、445 层、60 秒领袖战边际伤害（百万）。

## 接入方式

旧版模拟器使用 `botOpus2.js`，它依赖同版本 `bot.js` 的扩展接口：

```js
createBotPrepSteps
botPickBand
setUnitBonus
sellJunk
```

新版模拟器使用 `botOpusPolicy.js`，并由同版本 `bot.js` 消费：

```js
createBotPrepSteps({}, {
  core,
  target,
  searchTargets,
  buyBonus,
  canLevel,
})
```

新版适配层只使用公开的采购、冻结和升本 policy 钩子；新版 `bot.js` 的默认阵容、站位和领袖战排练仍由模拟器负责。新版没有 `setUnitBonus` 时，不要把旧文件直接当成兼容版本运行。目标模式的完整整局验证和 90% 胜率尚未完成，不能把模型估算或小样本结果写成验收结论。

## 运行边界

策略只读取自己的持有、可见商店、公开库存、队友公开盟约和当前回合公开敌人信息，不读取隐藏信息，也不修改游戏规则。

触发分类：

- 获得时：雪猎、灰喉、拜松、仇白、黍等；雪猎按每次获得给所属盟约 `+5` 层。
- 刷新时：拉普兰德、安洁莉娜。
- 本回合每获得一名干员：风絮、夕。
- 休整期或战斗持续：由 `STACKERS` 中的卡处理。

语法检查和 `git diff --check` 已通过；完整整局验证需使用与历史策略匹配的模拟器依赖后执行。
