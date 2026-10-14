/** 环境选择那两台登记的电脑（选环境那张表单、模型市场那几行都读它）。 */

/** 环境选择：两台登记的电脑，实验室那台是别人的，家里那台是读者自己的（离线）。 */
export const COMPUTE_DEVICES = [
  { device_id: 'lab', name: '实验室工作站', online: true, owned: false },
  { device_id: 'home', name: '家里那台', online: false, owned: true },
]
