/**
 * 断线后等多久再连：`ms` 的一半到全部之间随便取一个。服务器一重启，所有页面在同一刻
 * 断开；都等同样久，就会在同一刻一起重连、一起补读。
 */
export function retryAfter(ms: number): number {
  return ms - Math.floor((Math.random() * ms) / 2)
}
