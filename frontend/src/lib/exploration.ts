import type { DelveCarriedItem, ExplorationWithdrawalResult } from '@/types'
import { qualityName } from './quality'

function itemText(item: DelveCarriedItem & { quality_name?: string }): string {
  return `${item.quality_name || qualityName(item.quality)}${item.item?.name || item.item_id}×${item.quantity}`
}

/** 返程回执同时包含普通掉落、固定奖励和未使用道具，不能按生产任务的 quantity 读取。 */
export function explorationWithdrawalText(result: ExplorationWithdrawalResult): string {
  const rewards = [...(result.drops || []), ...(result.equipment_drops || [])]
    .filter((item) => item.quantity > 0)
    .map(itemText)
  if (result.guide_leaves) rewards.push(`引路枫叶×${result.guide_leaves}`)
  const summary = rewards.length
    ? `返程带回了${rewards.join('、')}`
    : `从第 ${result.depth} 段路线返程，本次没有战利品`
  const returned = (result.returned_items || []).filter((item) => item.quantity > 0).map(itemText)
  return returned.length ? `${summary}；退回未使用的${returned.join('、')}` : summary
}
