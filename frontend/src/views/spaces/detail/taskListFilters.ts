/** 列表的范围：全部、我参与的、我发布的。地址里 `filter` 缺省即全部。 */
export type TaskScope = 'all' | 'participating' | 'publishing'

export type TaskSortKey = 'latestPublished' | 'latestUpdated' | 'nearestDeadline'
