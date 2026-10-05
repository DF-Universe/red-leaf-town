<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Coins, Fish, Lock, Sailboat, Shovel, UsersRound, Waves } from 'lucide-vue-next'

import ConstructionCost from '@/components/ConstructionCost.vue'
import ActionButton from '@/components/ActionButton.vue'
import SlotPanel from '@/components/SlotPanel.vue'
import FishingPanel from '@/components/aquatic/FishingPanel.vue'
import PondCard from '@/components/aquatic/PondCard.vue'
import StateBlock from '@/components/StateBlock.vue'
import FacilityUpgradePanel from '@/components/FacilityUpgradePanel.vue'
import ViewHeader from '@/components/ViewHeader.vue'
import { useGameStore } from '@/stores/game'

const game = useGameStore()
const route = useRoute()
const router = useRouter()
const tabBar = ref<HTMLElement | null>(null)
const SailingPanel = defineAsyncComponent(() => import('@/components/aquatic/SailingPanel.vue'))
const tabs = computed(() => [
  { id: 'fishing', name: '钓鱼', icon: Fish, hint: '即刻下竿' },
  { id: 'ponds', name: '鱼塘', icon: Waves, hint: '长久经营' },
  ...(game.state?.sailing ? [{ id: 'sailing', name: '出海', icon: Sailboat, hint: '远航与发现' }] : []),
])
const activeTab = computed(() => tabs.value.some(tab => tab.id === route.query.tab) ? String(route.query.tab) : 'fishing')
const sailingReady = computed(() => Boolean(game.state?.sailing?.active_run && game.state.sailing.active_run.ready_at <= game.serverNow))

function selectTab(id: string) {
  void router.replace({ query: { ...route.query, tab: id } })
}

async function moveTab(event: KeyboardEvent) {
  const index = tabs.value.findIndex(tab => tab.id === activeTab.value)
  let next = index
  if (event.key === 'ArrowRight') next = (index + 1) % tabs.value.length
  else if (event.key === 'ArrowLeft') next = (index + tabs.value.length - 1) % tabs.value.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = tabs.value.length - 1
  else return
  event.preventDefault()
  await router.replace({ query: { ...route.query, tab: tabs.value[next].id } })
  await nextTick()
  tabBar.value?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus()
}

const aquatic = computed(() => game.state?.aquatic || null)
</script>

<template>
  <section v-if="game.state && aquatic" class="view-section aquatic-view">
    <ViewHeader eyebrow="RIVERS, PONDS & OPEN SEA" title="水产">
      <template #chip>
        <UsersRound :size="18" /> 水产编制 {{ game.state.industry_rules.aquatic?.partner_capacity || 0 }}
      </template>
    </ViewHeader>

    <StateBlock
      v-if="!aquatic.unlocked"
      :icon="Lock"
      title="水边还没有你的位置"
      :description="`居民等级 ${aquatic.next_spot_level || 2} 开放镇口小溪。`"
    />

    <template v-else>
      <nav ref="tabBar" class="aquatic-tabs" role="tablist" aria-label="水产玩法" @keydown="moveTab">
        <button
          v-for="tab in tabs" :id="`aquatic-tab-${tab.id}`" :key="tab.id" type="button" role="tab"
          :aria-selected="activeTab === tab.id" :aria-controls="`aquatic-panel-${tab.id}`"
          :tabindex="activeTab === tab.id ? 0 : -1" :class="{ selected: activeTab === tab.id }"
          @click="selectTab(tab.id)"
        >
          <component :is="tab.icon" :size="22" />
          <span><strong>{{ tab.name }}</strong><small>{{ tab.hint }}</small></span>
          <i v-if="tab.id === 'sailing' && sailingReady" class="tab-ready">已回港</i>
        </button>
      </nav>

      <div v-show="activeTab === 'fishing'" id="aquatic-panel-fishing" role="tabpanel" aria-labelledby="aquatic-tab-fishing">
        <FishingPanel :aquatic="aquatic" />
      </div>

      <div v-show="activeTab === 'ponds'" id="aquatic-panel-ponds" class="aquatic-pond-content" role="tabpanel" aria-labelledby="aquatic-tab-ponds">

      <section class="ponds">
        <header class="ponds-heading">
          <h2>鱼塘</h2>
          <p>鱼塘持续运转，不需要守候，离线期间同样计算，捞鱼也不消耗体力。投下的鱼苗要养满三个繁殖周期才长成可捞的成鱼，此后每个周期按成鱼数繁殖，新出生的鱼同样从鱼苗养起。成鱼数保持在稳态线以上时，世代加值持续累积，捞起的鱼品质更好；跌破稳态线则逐周期回落。饲料槽见底时全塘停摆，鱼苗也停止生长。</p>
        </header>

        <div class="pond-grid">
          <PondCard
            v-for="pond in aquatic.ponds"
            :key="pond.pond_id"
            :pond="pond"
            :aquatic="aquatic"
          />

          <article
            v-for="site in aquatic.buildable_ponds"
            :key="site.id"
            class="pond-site surface-card"
            :style="{ '--pond-accent': site.accent }"
          >
            <header>
              <h3>{{ site.name }}</h3>
              <small>{{ site.description }}</small>
            </header>
            <p class="pond-site-note">
              可容纳 {{ site.capacity }} 尾。挖塘是一次性投入，此后只需负担饲料。
            </p>
            <ConstructionCost
              class="pond-site-requirements"
              :coins="site.build_cost"
              :materials="site.build_materials"
              :coins-affordable="!site.unlocked || game.state.player.coins >= site.build_cost"
            />
            <p v-if="!site.unlocked" class="pond-site-locked">
              <Lock :size="13" />居民等级 {{ site.min_level }} 才能挖这口塘（当前 Lv.{{ game.state.player.level }}）
            </p>
            <p v-else-if="!site.affordable" class="pond-site-locked">
              <Coins :size="13" />建造所需红叶币或材料不足
            </p>
            <ActionButton
              :action-key="`aquatic:pond:${site.id}:build`"
              :disabled="!site.unlocked || !site.affordable"
              :reason="!site.unlocked ? `居民等级 ${site.min_level} 解锁` : (!site.affordable ? '红叶币或材料不足' : undefined)"
              @click="game.buildPond(site.id)"
            >
              <Shovel :size="15" /> 挖塘
            </ActionButton>
          </article>
        </div>
      </section>

      <FacilityUpgradePanel kind="feed" />
      <SlotPanel
        :slot="aquatic.feed_slot"
        description="鱼塘每走完一个繁殖周期从这里扣一次饲料，与周期长短无关；槽内耗尽则停摆，买不起的周期不会推进。饲料的品质分越高，鱼塘产出的品质越好。"
      />
      </div>

      <div v-if="game.state.sailing" v-show="activeTab === 'sailing'" id="aquatic-panel-sailing" role="tabpanel" aria-labelledby="aquatic-tab-sailing">
        <SailingPanel />
      </div>
    </template>
  </section>
</template>

<style scoped>
.aquatic-pond-content { display: flex; flex-direction: column; gap: 30px; }
.aquatic-view { display: flex; flex-direction: column; gap: 30px; }
.ponds-heading h2 { margin: 0; font: 600 18px Georgia, 'Noto Serif SC', serif; }
.ponds-heading p { margin: 6px 0 16px; color: #849087; font-size: 13px; line-height: 1.6; }
.pond-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.pond-site { --pond-accent: #4f8f9c; display: flex; flex-direction: column; gap: 12px; padding: 18px; border-style: dashed; border-color: color-mix(in srgb, var(--pond-accent) 26%, transparent); }
.pond-site h3 { margin: 0; font: 600 17px Georgia, 'Noto Serif SC', serif; }
.pond-site header small { display: block; margin-top: 5px; color: #849087; line-height: 1.55; }
.pond-site-requirements { margin-top: auto; }
.pond-site-note { margin: 0; color: #7d887f; font-size: 12px; line-height: 1.6; }
.pond-site-locked { display: flex; align-items: center; gap: 6px; margin: -4px 0 0; color: #a4736a; font-size: 12px; line-height: 1.5; }
.aquatic-tabs { display: flex; gap: 8px; padding: 7px; border: 1px solid #8cb7b826; border-radius: 18px; background: #0e1a17; }
.aquatic-tabs button { position: relative; display: flex; align-items: center; gap: 12px; flex: 1; min-width: 0; padding: 16px 20px; color: #98aca7; text-align: left; border: 1px solid transparent; border-radius: 12px; background: transparent; cursor: pointer; transition: background .18s, border-color .18s; }
.aquatic-tabs button:hover { color: #e4f0e6; background: #ffffff06; }
.aquatic-tabs button.selected { color: #d7eeea; border-color: #81c4c346; background: linear-gradient(120deg, #29504b85, #253b3645); box-shadow: 0 3px 14px #0002; }
.aquatic-tabs button:focus-visible { outline: 2px solid #a8d9d1; outline-offset: 2px; }
.aquatic-tabs strong { display: block; font-size: 16px; font-weight: 650; }
.aquatic-tabs small { display: block; margin-top: 4px; color: #8baba5; font-size: 12px; }
.tab-ready { margin-left: auto; padding: 3px 7px; border-radius: 6px; font-size: 12px; font-style: normal; white-space: nowrap; }
.tab-ready { color: #d4efb6; background: #78975424; }
@media (max-width: 760px) {
  .aquatic-view { gap: 22px; }
  .aquatic-tabs { gap: 4px; padding: 5px; border-radius: 14px; }
  .aquatic-tabs button { flex-direction: column; justify-content: center; gap: 7px; padding: 13px 5px; text-align: center; }
  .aquatic-tabs strong { font-size: 14px; }
  .aquatic-tabs small { display: none; }
  .tab-ready { margin: 0; padding: 0; background: transparent; }
  .aquatic-view .pond-grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
