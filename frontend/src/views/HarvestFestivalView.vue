<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { BookOpen, Check, ChefHat, Hourglass, Leaf, Lock, Shirt, Utensils } from 'lucide-vue-next'
import { api } from '@/api'
import GameIcon from '@/components/GameIcon.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import StateBlock from '@/components/StateBlock.vue'
import ViewHeader from '@/components/ViewHeader.vue'
import RewardChips from '@/components/RewardChips.vue'
import { useGameStore } from '@/stores/game'
import { useStoryStore } from '@/stores/story'
import type { HarvestDish, HarvestState, HarvestTable, StoryCueResult, StoryScript } from '@/types'

const game = useGameStore()
const story = useStoryStore()
const snap = ref<HarvestState | null>(null)
const loading = ref(true)
const busy = ref('')
const playing = ref('')
const watchedStory = ref(false)

const visible = computed(() => snap.value?.visible)
const todayText = computed(() => (snap.value ? `${snap.value.today} · 每日能交 ${snap.value.daily_limit} 份` : ''))

function date(value: number) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric',
  }).format(new Date(value * 1000))
}

async function load() {
  loading.value = true
  try {
    snap.value = await api<HarvestState>('/api/red-leaf-town/harvest/state')
  } catch (error) {
    game.showNotice(error instanceof Error ? error.message : '丰收祭暂时打不开')
  } finally {
    loading.value = false
  }
}

function key(table: HarvestTable, dish: HarvestDish) {
  return `${table.id}:${dish.item_id}`
}

function canSend(table: HarvestTable, dish: HarvestDish) {
  return table.unlocked && !table.filled && dish.remaining > 0 && table.today_left > 0 && dish.owned > 0
}

function sendable(table: HarvestTable, dish: HarvestDish) {
  return Math.min(dish.remaining, table.today_left, dish.owned)
}

async function send(table: HarvestTable, dish: HarvestDish, quantity: number) {
  const count = Math.max(1, Math.floor(quantity))
  if (!count || busy.value) return
  busy.value = key(table, dish)
  try {
    const result = await api<{ result: { table_filled: boolean; granted: unknown }; state: HarvestState }>(
      '/api/red-leaf-town/harvest/submit',
      {
        method: 'POST',
        body: JSON.stringify({
          table_id: table.id,
          dish_id: dish.item_id,
          quantity: count,
        }),
      },
    )
    // 后端若是回了个不带 tables 的整体状态（字段名撞车），不要拿它顶掉活动快照
    if (Array.isArray(result.state?.tables)) snap.value = result.state
    if (result.result.table_filled) game.showNotice(`「${table.name}」摆满了`)
    await game.refresh(true)
  } catch (error) {
    game.showNotice(error instanceof Error ? error.message : '交菜失败')
  } finally {
    busy.value = ''
  }
}

async function play(cue: string, storyId: string) {
  if (playing.value || story.active || !cue) return
  playing.value = storyId
  try {
    const result = await api<StoryCueResult>('/api/red-leaf-town/story/cue', {
      method: 'POST',
      body: JSON.stringify({ cue }),
    })
    if (result.stories.length) {
      story.enqueue(result.stories)
      // 剧本看完会把 seen 写回存档，这里等剧情收场再拉一次活动快照，
      // 否则「已读 / 已成礼」要手动刷新页面才变。
      watchedStory.value = true
    } else {
      // 看过的一次性剧本 cue 不再返回（上游按 seen 过滤），改走只读回看通道。
      const replay = await api<{ story: StoryScript; seen: boolean }>(
        `/api/red-leaf-town/story/replay/${encodeURIComponent(storyId)}`,
        { method: 'POST' },
      )
      story.preview(replay.story)
    }
  } catch (error) {
    // 剧本文件还没写的那些（比如谢秋大祭），回看通道会回 404，说的是同一件事
    const message = error instanceof Error ? error.message : ''
    game.showNotice(message === '剧情不存在' ? '这一幕还没写好，等下一版' : (message || '剧情暂时无法加载'))
  } finally {
    playing.value = ''
  }
}

watch(
  () => story.active,
  (active) => {
    if (active || !watchedStory.value) return
    watchedStory.value = false
    load()
  },
)

async function claimSkin(skinId: string) {
  if (busy.value) return
  busy.value = `skin:${skinId}`
  try {
    const result = await api<{ result: { skin_id: string; duplicate: boolean; name: string }; state: HarvestState }>(
      `/api/red-leaf-town/harvest/skins/${skinId}/claim`,
      { method: 'POST' },
    )
    if (Array.isArray(result.state?.tables)) snap.value = result.state
    game.showNotice(result.result.duplicate ? '这套立绘已经解锁过了' : `「${result.result.name}」解锁，可在伙伴页换上`)
    await game.refresh(true)
  } catch (error) {
    game.showNotice(error instanceof Error ? error.message : '兑换失败')
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <section v-if="game.state" class="view-section">
    <!-- slot 在 ViewHeader 里是静态插槽，数据到齐后换 key 强制重建，否则状态文案会停在首帧 -->
    <ViewHeader :key="snap ? `ready-${snap.active}` : 'loading'" eyebrow="HARVEST FESTIVAL" title="丰收祭">
      <template #chip><Leaf :size="18" />{{ snap?.active ? '活动进行中' : snap?.visible ? '已结束' : '未开放' }}</template>
    </ViewHeader>

    <StateBlock v-if="loading" title="正在支起长桌…" />
    <StateBlock v-else-if="!visible" title="活动尚未开放" />

    <template v-else-if="snap">
      <article class="harvest-card">
        <div class="harvest-heading">
          <div>
            <h2>把三张桌子摆满</h2>
            <p>蕾蕾号令开镰，洛纳莉掌灶。收秋货、做九道菜，一道一道端上去。</p>
          </div>
          <strong class="harvest-count">{{ snap.total_sent }}<small> / {{ snap.total_required }} 份</small></strong>
        </div>
        <ProgressBar :value="snap.total_required ? snap.total_sent / snap.total_required * 100 : 0" :height="8" color="var(--gold)" />
        <div class="harvest-meta">
          <span>活动至 {{ date(snap.ends_at) }}（北京时间）</span>
          <span>{{ todayText }}</span>
        </div>
        <p v-if="!snap.active" class="harvest-note">活动已结束，已摆上的菜与剧情仍可回看。</p>
      </article>

      <section class="harvest-section">
        <div class="harvest-heading"><h2><BookOpen :size="18" />剧情</h2><small>按桌子进度解锁</small></div>
        <div class="chapter-grid">
          <article
            v-for="chapter in snap.chapters"
            :key="chapter.story_id"
            class="harvest-card chapter-card"
            :class="{ 'is-locked': !chapter.unlocked && !chapter.pending, 'is-pending': chapter.pending }"
          >
            <div class="chapter-icon">
              <Hourglass v-if="chapter.pending" :size="18" />
              <Check v-else-if="chapter.seen" :size="18" />
              <BookOpen v-else-if="chapter.unlocked" :size="18" />
              <Lock v-else :size="18" />
            </div>
            <div class="chapter-copy">
              <h3>
                {{ chapter.title }}
                <span v-if="chapter.pending" class="chapter-pending">未完待续</span>
              </h3>
            </div>
            <button
              v-if="!chapter.pending"
              type="button"
              class="dish-button"
              :disabled="!chapter.unlocked || !!playing || story.active"
              @click="play(chapter.cue, chapter.story_id)"
            >{{ playing === chapter.story_id ? '加载中…' : chapter.seen ? '回看' : '观看' }}</button>
            <span v-else class="chapter-hold">待续</span>
          </article>
        </div>
      </section>

      <section v-for="table in snap.tables" :key="table.id" class="harvest-section">
        <article class="harvest-card table-card" :class="{ 'is-locked': !table.unlocked, 'is-filled': table.filled }">
          <div class="table-head">
            <div>
              <h3><Utensils :size="18" />{{ table.name }}</h3>
              <p>{{ table.subtitle }}</p>
            </div>
            <div class="table-stat">
              <strong>{{ table.sent }} / {{ table.required }}</strong>
              <span v-if="table.filled" class="table-tag is-done"><Check :size="13" />已摆满</span>
              <span v-else-if="!table.unlocked" class="table-tag"><Lock :size="13" />上一桌还没满</span>
              <span v-else class="table-tag">今日 {{ table.today_sent }}/{{ snap.daily_limit }}</span>
            </div>
          </div>

          <ul class="dish-list">
            <li v-for="dish in table.dishes" :key="dish.item_id" class="dish-row" :class="{ 'is-done': dish.done, 'is-short': !dish.done && dish.owned <= 0 }">
              <span class="dish-icon"><GameIcon :name="dish.icon" :size="18" /></span>
              <span class="dish-name">{{ dish.name }}</span>
              <span class="dish-progress">{{ dish.sent }} / {{ dish.required }}</span>
              <span class="dish-owned">库存 {{ dish.owned }}</span>
              <span class="dish-actions">
                <button
                  type="button"
                  class="dish-button"
                  :disabled="!canSend(table, dish) || busy === key(table, dish)"
                  @click="send(table, dish, 1)"
                >交 1</button>
                <button
                  v-if="sendable(table, dish) > 1"
                  type="button"
                  class="dish-button is-primary"
                  :disabled="!canSend(table, dish) || busy === key(table, dish)"
                  @click="send(table, dish, sendable(table, dish))"
                >交满 {{ sendable(table, dish) }}</button>
              </span>
            </li>
          </ul>

          <div class="table-foot">
            <span class="table-reward"><span class="table-reward-label">摆满奖励</span><RewardChips v-if="table.reward" :reward="table.reward" /></span>
          </div>
        </article>
      </section>


      <section class="harvest-section">
        <div class="harvest-heading"><h2><ChefHat :size="18" />祭坛</h2><small>洛纳莉主祭 · 不消耗供品</small></div>
        <div class="chapter-grid">
          <article v-for="rite in snap.rites" :key="rite.id" class="harvest-card chapter-card" :class="{ 'is-locked': !rite.unlocked }">
            <div class="chapter-icon"><Check v-if="rite.done" :size="18" /><Leaf v-else-if="rite.unlocked" :size="18" /><Lock v-else :size="18" /></div>
            <div class="chapter-copy">
              <h3>{{ rite.name }}</h3>
            </div>
            <button
              type="button"
              class="dish-button is-primary"
              :disabled="!rite.unlocked || rite.done || !!playing || story.active"
              @click="play(rite.cue, rite.story_id)"
            >{{ rite.done ? '已成礼' : playing === rite.story_id ? '加载中…' : '举行' }}</button>
          </article>
        </div>
      </section>

      <section v-if="snap.skins.length" class="harvest-section">
        <div class="harvest-heading"><h2><Shirt :size="18" />限定立绘</h2><small>用丰收祭材料兑换 · 每款限一次</small></div>
        <article v-for="skin in snap.skins" :key="skin.id" class="harvest-card skin-card" :class="{ 'is-unlocked': skin.unlocked }">
          <div class="skin-art">
            <img v-if="skin.url" :src="skin.url" :alt="skin.name" :class="{ 'is-locked': !skin.unlocked }" loading="lazy" />
            <div v-else class="skin-art-placeholder"><Lock :size="24" /></div>
          </div>
          <div class="skin-body">
            <h3>{{ skin.display_name }}<span class="skin-tag">{{ skin.name }}</span></h3>
            <div class="skin-cost">
              <span v-for="entry in skin.cost" :key="entry.item_id" class="cost-chip" :class="{ 'is-short': entry.owned < entry.quantity }">
                {{ entry.item_name }} {{ Math.min(entry.owned, entry.quantity) }}/{{ entry.quantity }}
              </span>
            </div>
            <div class="skin-actions">
              <span v-if="skin.unlocked" class="skin-done"><Check :size="15" />已解锁</span>
              <button
                v-else
                type="button"
                class="dish-button is-primary"
                :disabled="!skin.can_claim || busy === `skin:${skin.id}`"
                @click="claimSkin(skin.id)"
              >{{ !snap.active ? '活动已结束' : busy === `skin:${skin.id}` ? '兑换中…' : skin.can_claim ? '兑换解锁' : '材料不足' }}</button>
            </div>
          </div>
        </article>
        <p class="harvest-note">解锁后在伙伴详情页可以把它设为当前立绘。</p>
      </section>

      <details class="harvest-card harvest-rules">
        <summary>活动规则</summary>
        <p>① 采集点「落日晒谷场 / 桂香栗林」为活动限定，一轮约十件，不耗体力。蜜薯藤苗也在晒谷场掉落。</p>
        <p>② 九道菜在「丰收灶台」上做，灶台是独立工位，与镇民工坊各排各的队。</p>
        <p>③ 每张桌子每天最多交 {{ snap.daily_limit }} 份，不累积、不补交，跨天清零。</p>
        <p>④ 桌 2 要等桌 1 摆满，桌 3 要等桌 2 摆满。填满一桌当场发奖并解锁下一幕。</p>
        <p>⑤ 团圆席的丰收大拼盘要额外消耗四道成品菜，留好再交。</p>
      </details>
    </template>
  </section>
</template>

<style scoped>
.view-section { display: grid; gap: 20px; }
.harvest-card {
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 22px 7px 22px 7px;
  background: linear-gradient(155deg, #263328, #18211c);
}
.harvest-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.harvest-heading h2 { display: flex; align-items: center; gap: 8px; margin: 0 0 6px; font-size: 17px; color: #f0ecdd; }
.harvest-heading h3 { display: flex; align-items: center; gap: 8px; margin: 0 0 4px; font-size: 15px; color: #f0ecdd; }
.harvest-heading p { margin: 0; font-size: 13px; line-height: 1.6; color: #a9b3a6; }
.harvest-heading small { font-size: 12px; color: #8e988c; }
.harvest-count { font-size: 30px; line-height: 1; color: var(--gold); }
.harvest-count small { font-size: 13px; color: #8e988c; }
.harvest-meta { display: flex; flex-wrap: wrap; gap: 4px 18px; margin-top: 12px; font-size: 12px; color: #8e988c; }
.harvest-note { margin: 10px 0 0; font-size: 12.5px; line-height: 1.65; color: #9aa497; }
.harvest-section { display: grid; gap: 10px; }

.table-card.is-locked { opacity: .72; }
.table-card.is-filled { border-color: #6d8b52; }
.table-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; flex-wrap: wrap; }
.table-head p { margin: 0; font-size: 12.5px; color: #93a08f; }
.table-stat { display: grid; gap: 4px; justify-items: end; }
.table-stat strong { font-size: 15px; color: #e9e2ce; }
.table-tag { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; font-size: 11.5px; color: #a9b3a6; border: 1px solid #3c4a3b; border-radius: 99px; }
.table-tag.is-done { color: #cfe0a8; border-color: #6d8b52; }

.dish-list { list-style: none; margin: 14px 0 0; padding: 0; display: grid; gap: 6px; }
.dish-row {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) auto auto auto;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border: 1px solid #33402f;
  border-radius: 10px;
  background: #1d2620;
}
.dish-row.is-done { opacity: .62; }
.dish-row.is-short { border-color: #5a3a34; }
.dish-icon { display: grid; place-items: center; color: #cbbd8e; }
.dish-name { font-size: 13px; color: #e6e0cd; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dish-progress { font-size: 12px; color: #cbbd8e; font-variant-numeric: tabular-nums; }
.dish-owned { font-size: 11.5px; color: #8e988c; }
.dish-actions { display: flex; gap: 6px; }
.dish-button {
  padding: 5px 10px;
  font-size: 12px;
  color: #d9d3c0;
  border: 1px solid #45543f;
  border-radius: 8px;
  background: #26301f;
  cursor: pointer;
}
.dish-button.is-primary { color: #1d2419; border-color: var(--gold); background: var(--gold); font-weight: 700; }
.dish-button:disabled { opacity: .38; cursor: not-allowed; }

.table-foot { margin-top: 12px; padding-top: 12px; border-top: 1px dashed #33402f; }
.table-reward { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.table-reward-label { font-size: 11.5px; color: #8e988c; }

.chapter-grid { display: grid; gap: 10px; }
.chapter-card { display: flex; align-items: center; gap: 14px; padding: 14px 18px; }
.chapter-card.is-locked { opacity: .62; }
.chapter-card.is-pending { border-style: dashed; border-color: #453f2c; background: rgba(215, 173, 88, .035); }
.chapter-card.is-pending .chapter-icon { color: #b9a86f; border-style: dashed; }
.chapter-icon { display: grid; place-items: center; width: 34px; height: 34px; flex: none; color: #cbbd8e; border: 1px solid #3c4a3b; border-radius: 50%; }
.chapter-copy { flex: 1; min-width: 0; }
.chapter-copy h3 { margin: 0 0 3px; font-size: 14px; color: #f0ecdd; }
.chapter-copy h3:last-child { margin-bottom: 0; }
.chapter-copy p { margin: 0; font-size: 12px; line-height: 1.6; color: #93a08f; }
.chapter-pending {
  margin-left: 8px;
  padding: 1px 7px;
  border: 1px solid rgba(215, 173, 88, .34);
  border-radius: 999px;
  color: #cbbd8e;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.chapter-hold { flex: none; padding-right: 10px; color: #8b8467; font-size: 12px; letter-spacing: .06em; }

.harvest-rules summary { cursor: pointer; font-size: 13px; color: #cbbd8e; }
.harvest-rules p { margin: 8px 0 0; font-size: 12.5px; line-height: 1.7; color: #9aa497; }

.skin-card { display: flex; gap: 16px; align-items: stretch; }
.skin-card.is-unlocked { border-color: #6d8b52; }
.skin-art { width: 132px; aspect-ratio: 9 / 16; flex: none; overflow: hidden; border-radius: 12px; background: #101713; }
.skin-art img { display: block; width: 100%; height: 100%; object-fit: cover; }
.skin-art img.is-locked { filter: blur(14px) saturate(.6) brightness(.7); transform: scale(1.08); }
.skin-art-placeholder { display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; color: #82917e; }
.skin-body { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 10px; }
.skin-body h3 { margin: 0; font-size: 15px; color: #f0ecdd; }
.skin-tag { margin-left: 8px; font-size: 12.5px; font-weight: 600; color: #cbbd8e; }
.skin-cost { display: flex; flex-wrap: wrap; gap: 6px; }
.cost-chip { padding: 3px 10px; font-size: 12px; color: #c8d2c4; background: #1c2620; border: 1px solid var(--line); border-radius: 999px; }
.cost-chip.is-short { color: #d09b84; border-color: #6b4a3a66; }
.skin-actions { margin-top: auto; }
.skin-done { display: flex; align-items: center; gap: 5px; font-size: 13px; color: #a6c594; }

@media (max-width: 720px) {
  .dish-row { grid-template-columns: 24px minmax(0, 1fr) auto; row-gap: 6px; }
  .dish-owned { display: none; }
  .dish-actions { grid-column: 1 / -1; justify-content: flex-end; }
}
</style>
