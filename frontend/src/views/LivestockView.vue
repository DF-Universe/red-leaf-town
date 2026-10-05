<script setup lang="ts">
import { computed } from 'vue'
import { Coins, Hammer, Lock, UsersRound } from 'lucide-vue-next'

import ActionButton from '@/components/ActionButton.vue'
import FacilityCard from '@/components/livestock/FacilityCard.vue'
import GameIcon from '@/components/GameIcon.vue'
import FacilityUpgradePanel from '@/components/FacilityUpgradePanel.vue'
import SlotPanel from '@/components/SlotPanel.vue'
import StateBlock from '@/components/StateBlock.vue'
import ViewHeader from '@/components/ViewHeader.vue'
import { useGameStore } from '@/stores/game'

const game = useGameStore()

const livestock = computed(() => game.state?.livestock || null)
const feedSlot = computed(() => game.state?.aquatic.feed_slot || null)

function shortfall(coins: number) {
  return Math.max(0, coins - (game.player?.coins || 0))
}
</script>

<template>
  <section v-if="game.state && livestock && feedSlot" class="view-section livestock-view">
    <ViewHeader eyebrow="COOP AND BARN" title="畜牧">
      <template #chip>
        <UsersRound :size="18" /> 畜牧编制 {{ game.state.industry_rules.livestock?.partner_capacity || 0 }}
      </template>
    </ViewHeader>

    <StateBlock
      v-if="!livestock.facilities.length && !livestock.buildable_facilities.length"
      :icon="Lock"
      title="还没有地方养牲口"
      :description="`居民等级 ${livestock.next_facility_level || 4} 开放屋后的散养地。`"
    />

    <template v-else>
      <details class="livestock-help">
        <summary>饲养指南 · 每 8 小时结算，记得补充饲料</summary>
        <p class="livestock-intro">
        畜牧持续运转，不需要守候，离线期间同样计算，收取也不消耗体力。畜牧周期恒为 8 小时，不受能力与伙伴影响。幼崽养满对应周期数后成年，此后每个周期产出一次；待收产出攒到溢出上限即停产，也不再消耗饲料。产出的品质在每个周期结算时定下，收取时不再变化。照料每次消耗 1 点体力，提高亲密度，亲密度进产出品质，满值后有概率额外产出。饲料槽见底时整栏停摆，牲畜也停止成长。
        </p>
      </details>

      <div class="facility-grid">
        <FacilityCard
          v-for="facility in livestock.facilities"
          :key="facility.facility_id"
          :facility="facility"
          :livestock="livestock"
          :feed-slot="feedSlot"
        />

        <article
          v-for="site in livestock.buildable_facilities"
          :key="site.facility_id"
          class="facility-site surface-card"
          :style="{ '--facility-accent': site.accent }"
        >
          <header>
            <h3>{{ site.name }}</h3>
            <small>{{ site.description }}</small>
          </header>
          <p class="facility-site-note">
            可容纳 {{ site.capacity }} 只，饲料槽容量 +{{ site.feed_slot_capacity_bonus }}。
            <template v-if="site.replaces">建成后散养地回收，其中的牲畜自动迁入。</template>
          </p>

          <ul class="facility-site-cost">
            <li>
              <Coins :size="15" />
              <span>红叶币</span>
              <b :class="{ 'is-short': shortfall(site.build_coins) > 0 }">{{ site.build_coins }}</b>
            </li>
            <li v-for="material in site.build_materials" :key="material.item_id">
              <GameIcon :name="material.item.icon" :size="15" />
              <span>{{ material.item.name }}</span>
              <b :class="{ 'is-short': material.owned < material.quantity }">
                {{ material.owned }} / {{ material.quantity }}
              </b>
            </li>
          </ul>

          <p v-if="!site.unlocked" class="facility-site-locked">
            <Lock :size="13" /> 居民等级 {{ site.min_level }} 才能动土（当前 Lv.{{ game.state.player.level }}）
          </p>

          <ActionButton
            :action-key="`livestock:${site.facility_id}:build`"
            :disabled="!site.unlocked || !site.affordable"
            :reason="!site.unlocked ? `居民等级 ${site.min_level} 解锁` : (!site.affordable ? '红叶币或材料不足' : undefined)"
            @click="game.buildLivestockFacility(site.facility_id)"
          >
            <Hammer :size="15" /> 建造
          </ActionButton>
        </article>
      </div>

      <FacilityUpgradePanel kind="feed" />
      <SlotPanel
        :slot="feedSlot"
        description="畜牧每个周期按栏中牲畜各自的份数扣一次，攒满溢出上限的牲畜不再消耗；槽内耗尽则整栏停摆。饲料的品质分越高，畜产品的品质越好。"
      />
    </template>
  </section>
</template>

<style scoped>
.livestock-view { display: flex; flex-direction: column; gap: 16px; }
.livestock-help summary { color: #929d94; font-size: 12px; cursor: pointer; }
.livestock-help[open] summary { margin-bottom: 8px; }
.livestock-intro { margin: 0; color: #849087; font-size: 13px; line-height: 1.8; }

.facility-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr)); gap: 16px; align-items: start; }

.facility-site { --facility-accent: #b3a271; display: flex; flex-direction: column; gap: 12px; padding: 18px; border-style: dashed; border-color: color-mix(in srgb, var(--facility-accent) 26%, transparent); }
.facility-site h3 { margin: 0; font: 600 17px Georgia, 'Noto Serif SC', serif; }
.facility-site header small { display: block; margin-top: 5px; color: #849087; line-height: 1.55; }
.facility-site-note { margin: 0; color: #7d887f; font-size: 12px; line-height: 1.6; }
.facility-site-cost { display: grid; gap: 7px; margin: auto 0 0; padding: 0; list-style: none; }
.facility-site-cost li { display: flex; align-items: center; gap: 8px; color: #849087; font-size: 12px; }
.facility-site-cost b { margin-left: auto; color: var(--gold); font-size: 14px; }
.facility-site-cost b.is-short { color: #b4635a; }
.facility-site-locked { display: flex; align-items: center; gap: 6px; margin: 0; color: #a4736a; font-size: 12px; }
</style>
