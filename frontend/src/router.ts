import { createRouter, createWebHistory } from 'vue-router'

import FarmView from '@/views/FarmView.vue'
import MarketView from '@/views/MarketView.vue'
import PartnersView from '@/views/PartnersView.vue'
import GatheringView from '@/views/GatheringView.vue'
import CraftingView from '@/views/CraftingView.vue'
import MiningView from '@/views/MiningView.vue'
import DashboardView from '@/views/DashboardView.vue'
import CommissionsView from '@/views/CommissionsView.vue'
import PortalsView from '@/views/PortalsView.vue'
import GachaView from '@/views/GachaView.vue'

export default createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/story-share/:id',
      name: 'story-share',
      component: () => import('@/views/StoryShareView.vue'),
      meta: { title: '分享剧情', standalone: true },
    },
    {
      path: '/sailing', redirect: { name: 'aquatic', query: { tab: 'sailing' } },
    },
    {
      path: '/admin/crops',
      name: 'admin-crops',
      component: () => import('@/views/AdminCropsView.vue'),
      meta: { title: '作物数值', admin: true },
    },
    {
      path: '/admin/partners',
      name: 'admin-partners',
      component: () => import('@/views/AdminPartnersView.vue'),
      meta: { title: '伙伴管理', admin: true },
    },
    {
      path: '/admin/mail',
      name: 'admin-mail',
      component: () => import('@/views/AdminMailView.vue'),
      meta: { title: '镇邮局', admin: true },
    },
    {
      path: '/admin/codes',
      name: 'admin-codes',
      component: () => import('@/views/AdminCodesView.vue'),
      meta: { title: '激活码', admin: true },
    },
    {
      path: '/admin/story',
      name: 'admin-story',
      component: () => import('@/views/AdminStoryView.vue'),
      meta: { title: '剧情素材', admin: true },
    },
    {
      path: '/story-editor',
      name: 'story-editor',
      component: () => import('@/views/StoryEditorView.vue'),
      meta: { title: '剧情编辑器', standalone: true },
    },
    { path: '/', name: 'dashboard', component: DashboardView, meta: { title: '总览' } },
    { path: '/summer', name: 'summer', component: () => import('@/views/SummerEventView.vue'), meta: { title: '夏夜潮祭' } },
    { path: '/farm', name: 'farm', component: FarmView, meta: { title: '农场' } },
    { path: '/gathering', name: 'gathering', component: GatheringView, meta: { title: '采集' } },
    {
      path: '/exploration',
      name: 'exploration',
      component: () => import('@/views/ExplorationView.vue'),
      meta: { title: '探索' },
    },
    { path: '/refining', name: 'refining', component: () => import('@/views/RefiningView.vue'), meta: { title: '精制房' } },
    { path: '/crafting', name: 'crafting', component: CraftingView, meta: { title: '加工' } },
    { path: '/mining', name: 'mining', component: MiningView, meta: { title: '矿产' } },
    {
      path: '/aquatic',
      name: 'aquatic',
      component: () => import('@/views/AquaticView.vue'),
      meta: { title: '水产' },
    },
    {
      path: '/livestock',
      name: 'livestock',
      component: () => import('@/views/LivestockView.vue'),
      meta: { title: '畜牧' },
    },
    { path: '/commissions', name: 'commissions', component: CommissionsView, meta: { title: '今日委托' } },
    { path: '/portals', name: 'portals', component: PortalsView, meta: { title: '传送门' } },
    { path: '/gacha', name: 'gacha', component: GachaView, meta: { title: '异界招募' } },
    { path: '/market', name: 'market', component: MarketView, meta: { title: '商店' } },
    { path: '/shop', redirect: { name: 'market' } },
    { path: '/inventory', redirect: { name: 'market', query: { tab: 'sell' } } },
    { path: '/partners/:partnerId?', name: 'partners', component: PartnersView, meta: { title: '伙伴仓库' } },
    { path: '/spirits/:partnerId?', redirect: (to) => ({ name: 'partners', params: { partnerId: to.params.partnerId } }) },
    { path: '/admin/spirits', redirect: { name: 'admin-partners' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
