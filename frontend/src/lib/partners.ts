import { industryName } from '@/lib/industries'
import type { GameState, OwnedPartner } from '@/types'

export function isPartnerAssigned(partner: OwnedPartner): boolean {
  return (
    partner.assigned_plot_slot !== null ||
    Boolean(partner.assigned_gathering_site_id) ||
    Boolean(partner.assigned_refining) ||
    Boolean(partner.assigned_crafting_station_id) ||
    Boolean(partner.assigned_mining_site_id)
  )
}

export function partnerAssignmentLabel(partner: OwnedPartner, state: GameState | null): string | null {
  if (partner.assigned_refining) return partner.locked ? '精制房 · 清空后可换人' : '精制房'
  const suffix = partner.locked ? ' · 任务中' : ''
  if (partner.assigned_plot_slot !== null) {
    return `${industryName('farming')} · 土地 ${partner.assigned_plot_slot + 1}${suffix}`
  }
  if (partner.assigned_gathering_site_id) {
    const site = state?.gathering_sites.find((entry) => entry.site_id === partner.assigned_gathering_site_id)
    return `${industryName('gathering')} · ${site?.definition?.name || partner.assigned_gathering_site_id}${suffix}`
  }
  if (partner.assigned_crafting_station_id) {
    const station = state?.crafting_stations.find((entry) => entry.station_id === partner.assigned_crafting_station_id)
    return `${industryName('crafting')} · ${station?.definition?.name || partner.assigned_crafting_station_id}${suffix}`
  }
  if (partner.assigned_mining_site_id) {
    const site = state?.mining_sites.find((entry) => entry.site_id === partner.assigned_mining_site_id)
    return `${industryName('mining')} · ${site?.definition?.name || partner.assigned_mining_site_id}${suffix}`
  }
  return null
}
