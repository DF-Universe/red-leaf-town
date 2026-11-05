<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Flame, Leaf, Percent, RotateCw, Sparkles, Stamp } from 'lucide-vue-next'

import ActionButton from '@/components/ActionButton.vue'
import StateBlock from '@/components/StateBlock.vue'
import ViewHeader from '@/components/ViewHeader.vue'
import CrossoverBanner from '@/components/gacha/CrossoverBanner.vue'
import GachaPoolDetailsDialog from '@/components/gacha/GachaPoolDetailsDialog.vue'
import GachaPoolSidebar from '@/components/gacha/GachaPoolSidebar.vue'
import type { GachaPoolSummary } from '@/components/gacha/GachaPoolCard.vue'
import GachaPoster from '@/components/gacha/GachaPoster.vue'
import GachaPullOverlay from '@/components/gacha/GachaPullOverlay.vue'
import GachaResultCard from '@/components/gacha/GachaResultCard.vue'
import type { GachaResultMeta } from '@/components/gacha/GachaResultCard.vue'
import { useGameStore } from '@/stores/game'
import { useUiStore } from '@/stores/ui'
import type { CrossoverCampaign, GachaDrop, GachaPoolState } from '@/types'

const game = useGameStore()
const ui = useUiStore()
const pools = computed(() => game.state?.gacha_pools || [])
const anyUnlocked = computed(() => pools.value.some((pool) => pool.unlocked))
const lowestMinLevel = computed(() => pools.value[0]?.min_level ?? 1)

const crossovers = ref<CrossoverCampaign[]>([])
const results = ref<GachaDrop[]>([])
const overlayOpen = ref(false)
const detailsDialogOpen = ref(false)
const selectedPoolId = ref<string | null>(null)
const activePool = computed(() => {
  const requested = pools.value.find((pool) => pool.pool_id === selectedPoolId.value)
  return requested || pools.value.find((pool) => pool.unlocked) || pools.value[0] || null
})
const activePoolId = computed(() => activePool.value?.pool_id || '')

function poolFeaturedName(pool: GachaPoolState) {
  if (!pool.featured_partner_id) return null
  return pool.catalog.find((entry) => entry.partner_id === pool.featured_partner_id)?.name
    || pool.featured_partner_id
}

function poolFeaturedFourStarName(pool: GachaPoolState) {
  if (!pool.featured_four_star_partner_id) return null
  return pool.catalog.find((entry) => entry.partner_id === pool.featured_four_star_partner_id)?.name
    || pool.featured_four_star_partner_id
}

function poolSubtitle(pool: GachaPoolState) {
  if (pool.max_pulls_per_player != null) {
    return `限定 ${pool.max_pulls_per_player} 抽 · 剩余 ${pool.remaining_pulls}`
  }
  // UP 池的名单里可能有限定伙伴，不能一律说成常驻。
  const featured = poolFeaturedName(pool)
  return featured ? `UP ${featured} · 名单 ${pool.catalog.length} 位` : `${pool.catalog.length} 位常驻伙伴`
}

const poolSummaries = computed<GachaPoolSummary[]>(() =>
  pools.value.map((pool) => ({
    id: pool.pool_id,
    title: pool.title,
    subtitle: poolSubtitle(pool),
    unlocked: pool.unlocked,
    fiveStarRate: `5★ ${((pool.rarity_probabilities[5] || 0) * 100).toFixed(1)}%`,
  })),
)

function selectPool(id: string) {
  selectedPoolId.value = id
}

const posterBackground = computed(() => {
  const pool = activePool.value
  if (pool?.background?.url) return pool.background.url
  const catalog = pool?.catalog || []
  const featured = [...catalog].sort((a, b) => b.rarity - a.rarity).find((entry) => entry.artwork?.url)
  return featured?.artwork?.url || null
})

const featuredPartnerName = computed(() => (activePool.value ? poolFeaturedName(activePool.value) : null))

const pityTags = computed(() => {
  const pool = activePool.value
  if (!pool) return []
  const tags = [`至多 ${pool.pulls_until_four_star} 抽出现 4★+`, `至多 ${pool.pulls_until_five_star} 抽出现 5★`]
  if (featuredPartnerName.value) {
    tags.push(pool.featured_guaranteed
      ? `下个五星必出 ${featuredPartnerName.value}`
      : `UP！${featuredPartnerName.value} 占五星概率 ${(pool.featured_rate * 100).toFixed(0)}%，歪后下个五星必出 UP`)
  }
  const fourStarName = poolFeaturedFourStarName(pool)
  if (fourStarName) {
    const rate = (pool.rarity_probabilities[4] || 0) * pool.featured_four_star_rate * 100
    tags.push(`4★ UP！${fourStarName} 基础概率 ${rate.toFixed(1)}%`)
  }
  if (pool.max_pulls_per_player != null) tags.push(`本池限抽 ${pool.max_pulls_per_player} 次 · 剩余 ${pool.remaining_pulls}`)
  return tags
})

const singlePullDisabled = computed(() => activePool.value?.remaining_pulls === 0)
const tenPullDisabled = computed(() => {
  const remaining = activePool.value?.remaining_pulls
  return remaining !== null && remaining !== undefined && remaining < 10
})

/* ---- 轮替池：倒计时以服务端时间为准，本地只负责滴答 ---- */
const rotation = computed(() => activePool.value?.rotation || null)
const tick = ref(Date.now())
let syncAt = Date.now()
let tickTimer: number | null = null
watch(() => game.state?.server_time, () => { syncAt = Date.now() })

const serverNow = computed(() => {
  const base = game.state?.server_time
  if (!base) return tick.value / 1000
  return base + (tick.value - syncAt) / 1000
})

const rotationSecondsLeft = computed(() => {
  if (!rotation.value) return 0
  return Math.max(0, rotation.value.ends_at - serverNow.value)
})

const rotationCountdown = computed(() => {
  const total = Math.floor(rotationSecondsLeft.value)
  if (total <= 0) return '即将换期'
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (days > 0) return `${days} 天 ${hours} 小时`
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  return `${minutes} 分 ${seconds} 秒`
})

/* 倒计时归零后就地刷新一次，让池子自己翻页 */
watch(rotationSecondsLeft, (left, previous) => {
  if (left === 0 && previous > 0) void game.refresh(true)
})

onMounted(() => {
  tickTimer = window.setInterval(() => { tick.value = Date.now() }, 1000)
})
onUnmounted(() => {
  if (tickTimer !== null) window.clearInterval(tickTimer)
})

function recapMeta(drop: GachaDrop): GachaResultMeta {
  const pool = activePool.value
  if (drop.kind === 'partner') {
    const entry = pool?.catalog.find((item) => item.partner_id === drop.content_id)
    return { name: entry?.name || drop.content_id, rarity: drop.rarity ?? entry?.rarity ?? null, artwork: entry?.artwork, crop: entry?.avatar_crop }
  }
  const entry = pool?.task_items.find((item) => item.id === drop.content_id)
  return { name: entry?.name || drop.content_id, rarity: null }
}

async function loadCrossovers() {
  const payload = await game.loadCrossoverCampaigns()
  crossovers.value = payload?.campaigns || []
}

async function claimCrossover(campaignId: string) {
  if (await game.claimCrossover(campaignId)) await loadCrossovers()
}

onMounted(loadCrossovers)

async function pull(count: 1 | 10) {
  const pool = activePool.value
  if (!pool) return
  if (pool.remaining_pulls !== null && pool.remaining_pulls < count) {
    game.showNotice('这个招募池的次数已经用完啦')
    return
  }

  const times = count === 1 ? '一' : '十'
  const leaves = game.player?.guide_leaves || 0
  /* 缺多少就兑多少，在同一个确认框里问清楚，确认后自动兑换并开抽 */
  const shortfall = count - leaves
  const cost = shortfall * pool.maple_flame_per_leaf

  if (shortfall > 0) {
    const flame = game.player?.maple_flame || 0
    if (flame < cost) {
      game.showNotice(`枫火也不够了，还差 ${cost - flame} 枫火`)
      return
    }
  }

  const accepted = await ui.confirm({
    title: count === 1 ? '进行单次招募？' : '进行十连招募？',
    description: shortfall > 0
      ? `引路枫叶还差 ${shortfall} 片，将用 ${cost} 枫火兑换 ${shortfall} 片引路枫叶，并招募${times}次。`
      : `将消耗 ${count} 片引路枫叶招募${times}次，招募后剩余 ${leaves - count} 片。`,
    confirmLabel: '确定',
    cancelLabel: '取消',
  })
  if (!accepted) return

  if (shortfall > 0 && !(await game.convertMapleFlame(shortfall))) return

  const outcome = await game.recruit(count, pool.pool_id)
  if (outcome) {
    results.value = outcome.results
    overlayOpen.value = true
  }
}

</script>

<template>
  <section v-if="game.state && activePool" class="view-section gacha-view">
    <ViewHeader eyebrow="GUIDING LEAVES" title="异界招募">
      <template #chip><Sparkles :size="18" /> 招募名单 {{ activePool.catalog.length }} 位</template>
    </ViewHeader>

    <CrossoverBanner
      v-for="campaign in crossovers"
      :key="campaign.campaign_id"
      :campaign="campaign"
      @claim="claimCrossover"
    />

    <StateBlock
      v-if="!anyUnlocked"
      title="传送门还听不见你的呼唤"
      :description="`居民等级 ${lowestMinLevel} 开放异界招募。`"
    />

    <template v-else>
      <div class="gacha-wallet">
        <span><Flame :size="18" /><small>枫火</small><strong>{{ game.player?.maple_flame }}</strong></span>
        <span><Leaf :size="18" /><small>引路枫叶</small><strong>{{ game.player?.guide_leaves }}</strong></span>
        <span><Stamp :size="18" /><small>同行印记</small><strong>{{ game.player?.companion_marks }}</strong></span>
        <ActionButton
          variant="secondary"
          action-key="gacha:convert:1"
          :disabled="(game.player?.maple_flame || 0) < activePool.maple_flame_per_leaf"
          reason="枫火不足"
          @click="game.convertMapleFlame(1)"
        >{{ activePool.maple_flame_per_leaf }} 枫火兑换 1 片</ActionButton>
      </div>

      <div class="gacha-layout">
        <GachaPoolSidebar :pools="poolSummaries" :active-id="activePoolId" @select="selectPool">
          <template #footer>
            <p class="pool-sidebar-note">不限次数的招募池不会下架，可放心攒引路枫叶；新手招募池次数有限，抽完即止。</p>
          </template>
        </GachaPoolSidebar>

        <GachaPoster
          :title="activePool.title"
          :background-url="posterBackground"
          :pity-tags="pityTags"
        >
          <template #meta>
            <div v-if="rotation" class="poster-rotation">
              <p class="poster-rotation-now">
                <RotateCw :size="13" />
                <strong>{{ rotation.partner_name }}</strong>
                <em v-if="rotation.title">{{ rotation.title }}</em>
                <span class="poster-rotation-round">第 {{ rotation.round_no }} 轮</span>
              </p>
              <p v-if="rotation.tagline" class="poster-rotation-tagline">{{ rotation.tagline }}</p>
              <p class="poster-rotation-foot">
                <span>本期剩余 <b>{{ rotationCountdown }}</b></span>
                <span v-if="rotation.next_partner_name" class="poster-rotation-next">
                  下期 {{ rotation.next_partner_name }}<template v-if="rotation.next_title">「{{ rotation.next_title }}」</template>
                </span>
              </p>
            </div>
          </template>
          <template #actions>
            <span class="poster-balance"><Leaf :size="14" />引路枫叶 ×{{ game.player?.guide_leaves || 0 }}</span>
            <button type="button" class="text-button gacha-details-trigger" @click="detailsDialogOpen = true">
              <Percent :size="13" />查看概率详情
            </button>
            <ActionButton
              class="gacha-pull-button"
              action-key="gacha:single"
              :disabled="singlePullDisabled"
              reason="这个招募池的次数已经用完了"
              @click="pull(1)"
            >单次招募 · 1 片</ActionButton>
            <ActionButton
              class="gacha-pull-button"
              action-key="gacha:ten"
              :disabled="tenPullDisabled"
              reason="剩余次数不足以十连"
              @click="pull(10)"
            >十次招募 · 10 片</ActionButton>
          </template>
        </GachaPoster>
      </div>

      <div v-if="results.length" class="gacha-recap">
        <p class="gacha-recap-heading">最近获得</p>
        <div class="gacha-recap-grid">
          <GachaResultCard
            v-for="(drop, index) in results"
            :key="`recap:${index}:${drop.content_id}`"
            :drop="drop"
            :meta="recapMeta(drop)"
            revealed
            instant
          />
        </div>
      </div>
    </template>

    <GachaPullOverlay
      :open="overlayOpen"
      :results="results"
      :catalog="activePool.catalog"
      :task-items="activePool.task_items"
      @close="overlayOpen = false"
    />
    <GachaPoolDetailsDialog
      :open="detailsDialogOpen"
      :pool="activePool"
      @close="detailsDialogOpen = false"
    />
  </section>
</template>

<style scoped>
.gacha-wallet { display: grid; grid-template-columns: repeat(3, minmax(130px, 1fr)) auto; gap: 9px; margin-bottom: 14px; }
.gacha-wallet > span { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 12px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
.gacha-wallet svg { color: var(--gold); }
.gacha-wallet small { color: #849087; }

.gacha-layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 16px; align-items: start; }
.pool-sidebar-note { color: #7c877e; font-size: 11px; line-height: 1.6; }

/* 轮替信息（写在卡面里，不再是独立的一条） */
.poster-rotation {
  display: grid;
  gap: 5px;
  padding: 2px 0 2px 11px;
  border-left: 2px solid rgba(215, 173, 88, .72);
}
.poster-rotation-now { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 9px; margin: 0; }
.poster-rotation-now svg { align-self: center; color: var(--gold); }
.poster-rotation-now strong {
  color: #f8f2df;
  font-size: clamp(18px, 2vw, 22px);
  font-weight: 800;
  text-shadow: 0 2px 10px rgba(0, 0, 0, .55);
}
.poster-rotation-now em {
  padding: 2px 8px;
  border: 1px solid rgba(215, 173, 88, .45);
  border-radius: 999px;
  background: rgba(58, 46, 20, .72);
  color: #f0d79a;
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
  letter-spacing: .06em;
}
.poster-rotation-round {
  color: rgba(240, 234, 216, .66);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .08em;
}
.poster-rotation-tagline {
  margin: 0;
  color: rgba(243, 238, 224, .9);
  font-size: 12px;
  line-height: 1.65;
  text-shadow: 0 1px 7px rgba(0, 0, 0, .8);
}
.poster-rotation-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin: 2px 0 0;
  color: rgba(238, 232, 215, .82);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 1px 7px rgba(0, 0, 0, .8);
}
.poster-rotation-foot b { color: #f6e9c4; font-weight: 800; letter-spacing: .02em; }
.poster-rotation-next::before { content: '·'; margin-right: 8px; color: rgba(232, 226, 208, .4); }

.poster-balance { display: flex; align-items: center; gap: 6px; color: #c9d3c6; font-size: 12px; }
.poster-balance svg { color: var(--leaf-bright); }

.gacha-details-trigger { display: inline-flex; align-items: center; justify-content: center; gap: 5px; }
.gacha-details-trigger svg { color: var(--gold); }

.gacha-recap { margin-top: 20px; }
.gacha-recap-heading { margin: 0 0 10px; color: #77837a; font-size: 12px; font-weight: 800; letter-spacing: .12em; }
.gacha-recap-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 10px; }

@media (max-width: 900px) {
  .gacha-layout { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .gacha-wallet { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 720px) {
  .poster-balance { min-width: 0; flex-wrap: wrap; overflow-wrap: anywhere; }
  .gacha-details-trigger { justify-self: end; min-height: 36px; padding: 4px 0; font-size: 12px; }
  .gacha-pull-button { width: 100%; min-width: 0; min-height: 44px; padding: 10px 6px; font-size: 13px; }
}
</style>
