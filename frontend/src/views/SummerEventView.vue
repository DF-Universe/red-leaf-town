<script setup lang="ts">
import { computed, ref } from 'vue'
import { BookOpen, Check, Compass, Fish, Gift, Lock, Shirt, Sun, Trees } from 'lucide-vue-next'
import { api } from '@/api'
import ActionButton from '@/components/ActionButton.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import RewardChips from '@/components/RewardChips.vue'
import StateBlock from '@/components/StateBlock.vue'
import ViewHeader from '@/components/ViewHeader.vue'
import { useGameStore } from '@/stores/game'
import { useStoryStore } from '@/stores/story'
import type { StoryCueResult, SummerChapter } from '@/types'

const game = useGameStore()
const story = useStoryStore()
const event = computed(() => game.state?.summer_event)
const playing = ref('')
const claimable = computed(() => event.value?.milestones.filter(m => m.claimable).length || 0)
const places = [
  { name: '夏夜潮祭', to: '/exploration', level: 15, icon: Compass, copy: '探秘节点结算 · 每消耗 1 体力得 1 点' },
  { name: '夏夜灯湾', to: '/aquatic?tab=fishing', level: 12, icon: Fish, copy: '抛竿与搏鱼结算 · 每消耗 1 体力得 1 点' },
  { name: '夏夜萤野', to: '/gathering', level: 12, icon: Trees, copy: '领取完成任务 · 每小时任务时长得 2 点' },
]
function date(value: number) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value * 1000))
}
async function play(chapter: SummerChapter) {
  if (playing.value || story.active || !chapter.unlocked) return
  playing.value = chapter.story_id
  try {
    const result = await api<StoryCueResult>('/api/red-leaf-town/story/cue', { method: 'POST', body: JSON.stringify({ cue: chapter.cue }) })
    if (!result.stories.length) game.showNotice('剧情尚未解锁，请刷新进度后重试')
    story.enqueue(result.stories)
  } catch (error) {
    game.showNotice(error instanceof Error ? error.message : '剧情暂时无法加载')
  } finally { playing.value = '' }
}
</script>

<template>
  <section v-if="game.state" class="view-section">
    <ViewHeader eyebrow="SUMMER FESTIVAL" title="夏夜潮祭">
      <template #chip><Sun :size="18" />{{ event?.active ? '活动进行中' : '夏日回忆' }}</template>
    </ViewHeader>
    <StateBlock v-if="!event?.visible" title="活动尚未开放" />
    <template v-else>
      <article class="summer-card summer-progress">
        <div class="summer-heading">
          <div><h2>收集夏日的回忆</h2><p>参与活动场所，累积点数，解锁剧情与回礼。</p></div>
          <strong class="point-count">{{ Math.floor(event.points) }}<small> / {{ event.target }} 点</small></strong>
        </div>
        <ProgressBar :value="Math.min(100, event.points / event.target * 100)" :height="8" color="var(--gold)" />
        <div class="summer-meta"><span>活动至 {{ date(event.ends_at) }}（北京时间）</span><span>奖励可领至 {{ date(event.claim_ends_at) }}</span></div>
        <p v-if="!event.active" class="summer-note">活动积点已结束。{{ event.can_claim ? '达标奖励仍可领取，已解锁剧情可继续观看。' : '领奖期已结束，已解锁剧情可继续回看。' }}</p>
        <p v-else class="summer-note">1000 点领取全部奖励 · 点数不消耗 · 采集小数进度累计保留</p>
      </article>

      <div class="summer-places">
        <article v-for="place in places" :key="place.name" class="summer-card place-card">
          <component :is="place.icon" :size="22" class="summer-icon" />
          <div><h3>{{ place.name }}</h3><p>{{ place.copy }}</p></div>
          <RouterLink v-if="event.active && game.state.player.level >= place.level" :to="place.to" class="secondary-button">前往</RouterLink>
          <span v-else class="summer-muted">{{ event.active ? `${place.level} 级开放` : '已结束' }}</span>
        </article>
      </div>

      <section class="summer-section" aria-labelledby="summer-story-title">
        <div class="summer-heading"><h2 id="summer-story-title"><BookOpen :size="19" />夏日故事</h2><small>共 {{ event.chapters.length }} 段剧情 · 按活动点数依次解锁</small></div>
        <div class="summer-chapters">
          <article v-for="chapter in event.chapters" :key="chapter.story_id" class="summer-card chapter-card" :class="{ 'is-locked': !chapter.unlocked }">
            <div class="chapter-icon"><Check v-if="chapter.seen" :size="19" /><BookOpen v-else-if="chapter.unlocked" :size="19" /><Lock v-else :size="19" /></div>
            <div class="chapter-copy"><h3>{{ chapter.title }}</h3><p>{{ chapter.points ? `${chapter.points} 点解锁` : '免费开启' }}<span v-if="!chapter.previous_seen"> · 请先观看前一章</span><span v-else-if="chapter.seen"> · 已读</span></p></div>
            <ActionButton :action-key="`summer:story:${chapter.story_id}`" variant="secondary" :disabled="!chapter.unlocked || !!playing || story.active" @click="play(chapter)">{{ playing === chapter.story_id ? '加载中…' : chapter.seen ? '回看' : '观看' }}</ActionButton>
          </article>
        </div>
        <p class="summer-note">后续章节准备中。剧情与奖励分开领取，不必看完剧情才能获得回礼。</p>
      </section>

      <section class="summer-section" aria-labelledby="summer-reward-title">
        <div class="summer-heading"><h2 id="summer-reward-title"><Gift :size="19" />点数回礼</h2><small>{{ claimable ? `${claimable} 档待领取` : '累计 20 张同行叶 · 保底棱瓜果种子' }}</small></div>
        <div class="summer-rewards">
          <article v-for="milestone in event.milestones" :key="milestone.points" class="summer-card reward-row" :class="{ 'is-claimed': milestone.claimed }">
            <strong class="reward-threshold">{{ milestone.points }}<small>活动点</small></strong>
            <RewardChips :reward="milestone.reward" />
            <span v-if="milestone.claimed" class="reward-done"><Check :size="15" />已领取</span>
            <ActionButton v-else :action-key="`summer:claim:${milestone.points}`" :disabled="!milestone.claimable" :variant="milestone.claimable ? 'primary' : 'secondary'" @click="game.claimSummerReward(milestone.points)">{{ !event.can_claim ? '领奖已结束' : milestone.claimable ? '领取' : '未达标' }}</ActionButton>
          </article>
        </div>
      </section>
      <section v-if="event.skins?.length" class="summer-section" aria-labelledby="summer-skin-title">
        <div class="summer-heading"><h2 id="summer-skin-title"><Shirt :size="19" />夏日限定</h2><small>用活动物产兑换限定立绘 · 每款限一次</small></div>
        <article v-for="skin in event.skins" :key="skin.id" class="summer-card skin-card" :class="{ 'is-unlocked': skin.unlocked }">
          <div class="skin-art">
            <img v-if="skin.url" :src="skin.url" :alt="skin.name" :class="{ 'is-locked': !skin.unlocked }" loading="lazy" />
            <div v-else class="skin-art-placeholder"><Lock :size="26" /></div>
          </div>
          <div class="skin-body">
            <h3>{{ skin.display_name || skin.name }}<span class="skin-tag">{{ skin.name }}</span></h3>
            <div class="skin-cost">
              <span v-for="entry in skin.cost" :key="entry.item_id" class="cost-chip" :class="{ 'is-short': entry.owned < entry.quantity }">
                {{ entry.item_name }} {{ Math.min(entry.owned, entry.quantity) }}/{{ entry.quantity }}
              </span>
            </div>
            <div class="skin-actions">
              <span v-if="skin.unlocked" class="reward-done"><Check :size="15" />已解锁</span>
              <ActionButton v-else :action-key="`summer:skin:${skin.id}`" :disabled="!skin.can_claim" :variant="skin.can_claim ? 'primary' : 'secondary'" @click="game.claimSummerSkin(skin.id)">
                {{ !event.active ? '活动已结束' : skin.can_claim ? '兑换解锁' : '材料不足' }}
              </ActionButton>
            </div>
          </div>
        </article>
      </section>
      <details class="summer-card summer-rules"><summary>点数规则</summary><p>仅三个夏活场所计分。探秘结算节点时按实际耗体计分，失败额外耗体也计入；战斗结束或成功逃跑时结算，已得点数不因全灭、撤离扣回。钓鱼追加搏鱼无论成功与否都按耗体计分。</p><p>采集按开工时冻结的实际任务时长计算，取消不给点，成熟后等待领取不加点。点数不受 SP 收益加成、物产品质或数量影响。活动结束后停止积点，已有任务仍可领取物产；奖励领取期额外保留 7 天。</p></details>
    </template>
  </section>
</template>

<style scoped>
.view-section { display: grid; gap: 20px; }
.summer-card { padding: 20px; border: 1px solid var(--line); border-radius: 22px 7px 22px 7px; background: linear-gradient(155deg, #263328, #18211c); }
.summer-progress { display: grid; gap: 16px; border-color: #b99b5455; background: linear-gradient(130deg, #3a3626, #202b23 65%); }
.summer-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
h2, h3, p { margin: 0; }
h2 { display: flex; align-items: center; gap: 8px; font-size: 17px; color: var(--cream); }
h3 { font-size: 15px; color: var(--cream); }
p, .summer-muted, .summer-heading > small { color: #a8b5a5; font-size: 13px; line-height: 1.7; }
.summer-heading p { margin-top: 7px; }
.point-count { color: var(--gold); font-size: 32px; font-variant-numeric: tabular-nums; }
.point-count small { font-size: 14px; font-weight: 400; color: #c0bea7; }
.summer-meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px; color: #abb6ab; font-size: 12px; }
.summer-note { font-size: 12px; color: #a6b79a; }
.summer-places { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.place-card { display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; align-items: center; gap: 12px; padding: 16px; }
.place-card > div { min-width: 0; }
.place-card p { margin-top: 6px; font-size: 12px; }
.summer-icon, .chapter-icon { color: var(--gold); }
.place-card .secondary-button { display: inline-flex; align-items: center; justify-content: center; align-self: center; font-size: 12px; line-height: 1; white-space: nowrap; text-decoration: none; }
.summer-section { display: grid; gap: 12px; }
.summer-chapters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.chapter-card { display: flex; align-items: center; gap: 12px; padding: 16px; }
.chapter-copy { flex: 1; min-width: 0; }
.chapter-copy p { font-size: 12px; margin-top: 4px; }
.is-locked .chapter-icon { color: #82917e; }
.summer-rewards { display: grid; gap: 8px; }
.reward-row { display: grid; grid-template-columns: 64px minmax(0, 1fr) auto; align-items: center; gap: 16px; padding: 16px 20px; }
.reward-threshold { color: var(--gold); font-size: 20px; }
.reward-threshold small { display: block; font-size: 11px; color: #a8b5a5; font-weight: 400; margin-top: 3px; }
.reward-done { display: flex; align-items: center; gap: 5px; color: #a6c594; font-size: 13px; }
.is-claimed { background: #18211c; }
.skin-card { display: flex; gap: 16px; padding: 16px 20px; }
.skin-art { width: 120px; aspect-ratio: 9 / 16; border-radius: 12px; overflow: hidden; background: #101713; flex-shrink: 0; }
.skin-art img { width: 100%; height: 100%; object-fit: cover; display: block; }
.skin-art img.is-locked { filter: blur(14px) saturate(0.6) brightness(0.7); transform: scale(1.08); }
.skin-art-placeholder { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; color: #82917e; }
.skin-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
.skin-tag { margin-left: 8px; font-size: 13px; font-weight: 600; color: #8ab4f8; }
.skin-cost { display: flex; flex-wrap: wrap; gap: 6px; }
.cost-chip { font-size: 12px; padding: 3px 10px; border: 1px solid var(--line); border-radius: 999px; color: #c8d2c4; background: #1c2620; }
.cost-chip.is-short { color: #d09b84; border-color: #6b4a3a66; }
.skin-actions { margin-top: auto; }
.summer-rules { font-size: 13px; color: #acbaa6; }
.summer-rules summary { cursor: pointer; color: var(--cream); }
.summer-rules p { margin-top: 10px; }
@media (max-width: 1000px) { .summer-places { grid-template-columns: 1fr; } }
@media (max-width: 600px) {
  .summer-card { padding: 16px; }
  .summer-chapters { grid-template-columns: 1fr; }
  .reward-row { grid-template-columns: 48px minmax(0, 1fr); gap: 10px; }
  .reward-row > button, .reward-done { grid-column: 2; justify-self: end; }
  .summer-heading > small { font-size: 12px; }
}
</style>
