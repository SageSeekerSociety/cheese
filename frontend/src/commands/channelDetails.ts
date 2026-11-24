// 「频道详情」那张对话框长在频道页头上（TopicHeader）。侧栏右键、命令面板里点「频道
// 详情」时，页头可能还没挂上（人在别的频道、在任务页上）：先去那个频道，再由页头接着
// 打开。这一格就是两边交接的那一下：谁要开就写进来，那个频道的页头看见了就打开并清掉。
import { shallowRef } from 'vue'

export type ChannelDetailsTab = 'about' | 'members' | 'settings'

export const channelDetailsRequest = shallowRef<{ topicId: string; tab: ChannelDetailsTab } | null>(null)

export function requestChannelDetails(topicId: string, tab: ChannelDetailsTab = 'about'): void {
  channelDetailsRequest.value = { topicId, tab }
}
