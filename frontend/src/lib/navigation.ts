import type { Component } from 'vue'
import { Beef, Compass, DoorOpen, Flame, FlaskConical, Hammer, HandHeart, LayoutDashboard, Pickaxe, Sparkles, Sprout, Store, Sun, Trees, Waves } from 'lucide-vue-next'

import type { GameState } from '@/types'

export type ReadyGroup = 'plots' | 'gathering' | 'mining' | 'crafting'

export interface NavItem {
  to: string
  label: string
  icon: Component
  primary: boolean
  accent?: 'festival'
  readyGroup?: ReadyGroup
  unlocked?: (state: GameState) => boolean
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '总览', icon: LayoutDashboard, primary: true },
  { to: '/summer', label: '夏夜潮祭', icon: Sun, primary: false, accent: 'festival', unlocked: (state) => !!state.summer_event?.visible },
  { to: '/farm', label: '农场', icon: Sprout, primary: true, readyGroup: 'plots' },
  {
    to: '/gathering',
    label: '采集',
    icon: Trees,
    primary: true,
    readyGroup: 'gathering',
    unlocked: (state) => state.gathering_sites.length > 0,
  },
  {
    to: '/exploration',
    label: '探索',
    icon: Compass,
    primary: false,
    unlocked: (state) => state.exploration.unlocked,
  },
  {
    to: '/crafting',
    label: '加工',
    icon: Hammer,
    primary: false,
    readyGroup: 'crafting',
    unlocked: (state) => state.crafting_stations.length > 0,
  },
  { to: '/refining', label: '精制房', icon: FlaskConical, primary: false, unlocked: (state) => !!state.facilities?.refining.unlocked },
  {
    to: '/mining',
    label: '矿产',
    icon: Pickaxe,
    primary: false,
    readyGroup: 'mining',
    unlocked: (state) => state.mining_sites.length > 0,
  },
  {
    to: '/aquatic',
    label: '水产',
    icon: Waves,
    primary: false,
    unlocked: (state) => state.aquatic.unlocked,
  },
  {
    to: '/livestock',
    label: '畜牧',
    icon: Beef,
    primary: false,
    unlocked: (state) => state.livestock.unlocked,
  },
  {
    to: '/commissions',
    label: '委托',
    icon: HandHeart,
    primary: false,
    unlocked: (state) => state.commissions.unlocked,
  },
  {
    to: '/portals',
    label: '传送门',
    icon: DoorOpen,
    primary: false,
    unlocked: (state) => state.portals.some((portal) => portal.unlocked),
  },
  { to: '/market', label: '商店', icon: Store, primary: true },
  { to: '/partners', label: '伙伴', icon: Sparkles, primary: false },
  { to: '/gacha', label: '招募', icon: Flame, primary: false, unlocked: (state) => state.gacha_pools.some((pool) => pool.unlocked) },
]

export function unlockedNavItems(state: GameState | null): NavItem[] {
  if (!state) return NAV_ITEMS.filter((item) => !item.unlocked)
  return NAV_ITEMS.filter((item) => !item.unlocked || item.unlocked(state))
}
