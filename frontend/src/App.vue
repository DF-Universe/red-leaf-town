<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ChevronRight, Copy, Inbox, Leaf, LogOut, Map, Menu, RefreshCw, RotateCw, Ticket, Trophy, UserRound, X } from 'lucide-vue-next'

import ActionButton from '@/components/ActionButton.vue'
import AppNav from '@/components/AppNav.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import CraftingOutcomeDialog from '@/components/CraftingOutcomeDialog.vue'
import AchievementPanel from '@/components/achievements/AchievementPanel.vue'
import MailInbox from '@/components/mail/MailInbox.vue'
import MonthlyCardDialog from '@/components/MonthlyCardDialog.vue'
import StaminaDialog from '@/components/StaminaDialog.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import ResourcePill from '@/components/ResourcePill.vue'
import StoryOverlay from '@/components/story/StoryOverlay.vue'
import { useGameStore } from '@/stores/game'
import { useStoryStore } from '@/stores/story'
import { useUiStore } from '@/stores/ui'

const game = useGameStore()
const story = useStoryStore()
const ui = useUiStore()
const route = useRoute()
const bindingCommand = ref('')

// 后台和剧情编辑器都不套玩家端的外壳：不拉存档、不轮询、不发剧情信号。
const isBareRoute = computed(() => route.meta.admin === true || route.meta.standalone === true)
const routeTitle = computed(() => String(route.meta.title || '红叶镇'))
// 未读优先，都读完了还留着没领的附件就换成金色提醒。
const mailBadge = computed(() => {
  const mail = game.state?.mail
  if (!mail) return null
  if (mail.unread) return { count: mail.unread, tone: 'unread' as const }
  return mail.unclaimed ? { count: mail.unclaimed, tone: 'unclaimed' as const } : null
})
const loginUrl = computed(() => `/api/oauth/red-leaf-town/start?next=${encodeURIComponent('/red-leaf-town/')}`)
const levelProgress = computed(() => {
  const player = game.player
  if (!player || !player.next_level_xp) return 100
  const floor = player.current_level_xp
  return Math.max(2, Math.min(100, ((player.experience - floor) / (player.next_level_xp - floor)) * 100))
})
// 月卡按钮只在「今天还能领」时挂角标，剩余天数不做角标——那是打开面板才关心的事。
const monthlyBadge = computed(() => (game.state?.monthly_card?.claimable ? '!' : ''))
const levelHint = computed(() => {
  const player = game.player
  if (!player) return ''
  return player.next_level_xp ? `${player.experience} / ${player.next_level_xp} XP` : '已达当前上限'
})

onMounted(async () => {
  if (isBareRoute.value) return
  await game.initialize()
})

watch(
  () => [game.status, route.name] as const,
  ([status, name]) => {
    if (status !== 'ready' || !name || isBareRoute.value) return
    story.cue(`view:${String(name)}`)
  },
  { immediate: true },
)

watch(
  () => ui.navOpen,
  (open) => {
    document.body.style.overflow = open ? 'hidden' : ''
  },
)

async function generateBindingCode() {
  const result = await game.createBindingCode()
  if (result) bindingCommand.value = result.command
}

async function copyBindingCommand() {
  if (!bindingCommand.value) return
  await navigator.clipboard.writeText(bindingCommand.value)
  game.showNotice('指令已复制')
}
</script>

<template>
  <RouterView v-if="isBareRoute" />

  <div v-else-if="game.status === 'checking'" class="splash-screen">
    <div class="brand-seal"><Leaf :size="42" /></div>
    <h1>红叶镇物语</h1>
    <p>正在沿着山路前往小镇……</p>
    <span class="loading-line"><i /></span>
  </div>

  <main v-else-if="game.status === 'guest' || game.status === 'error'" class="login-screen">
    <div class="login-landscape" aria-hidden="true"><i class="hill hill-one" /><i class="hill hill-two" /><i class="sun" /></div>
    <section class="login-card">
      <div class="brand-seal"><Leaf :size="38" /></div>
      <p class="eyebrow">WELCOME TO</p>
      <h1>红叶镇物语</h1>
      <p class="login-copy">在山谷的红叶落下之前，经营一片属于你的土地。离开时，时间仍会继续流动。</p>
      <p v-if="game.error" class="error-banner">{{ game.error }}</p>
      <button v-if="game.status === 'error'" class="oauth-button" @click="game.initialize()">
        <RotateCw :size="19" />
        重新连接红叶镇
        <ChevronRight :size="18" />
      </button>
      <a class="oauth-button" :class="{ 'oauth-button--muted': game.status === 'error' }" :href="loginUrl">
        <UserRound :size="19" />
        使用水鱼账号登录
        <ChevronRight :size="18" />
      </a>
      <small>每个水鱼账号对应一位红叶镇居民</small>
    </section>
  </main>

  <div v-else-if="game.state && game.player" class="game-shell">
    <div v-if="ui.navOpen" class="nav-scrim" @click="ui.navOpen = false" />

    <aside class="sidebar" :class="{ open: ui.navOpen }">
      <div class="sidebar-brand">
        <div class="brand-seal brand-seal--small"><Leaf :size="24" /></div>
        <div><strong>红叶镇物语</strong><small>RED LEAF TOWN</small></div>
        <button class="icon-button mobile-close" @click="ui.navOpen = false"><X :size="20" /></button>
      </div>

      <AppNav variant="rail" @navigate="ui.navOpen = false" />

      <div class="coming-soon">
        <Map :size="18" />
        <div><strong>小镇还在扩建</strong></div>
      </div>
      <button class="profile-button" @click="ui.accountOpen = true; ui.navOpen = false">
        <span class="avatar">{{ game.player.display_name.slice(0, 1) }}</span>
        <span><strong>{{ game.player.display_name }}</strong><small>Lv.{{ game.player.level }} · {{ levelHint }}</small></span>
        <ChevronRight :size="17" />
      </button>
    </aside>

    <div class="main-column">
      <header class="topbar">
        <button class="icon-button menu-button" @click="ui.navOpen = true"><Menu :size="21" /></button>
        <div class="page-location"><small>当前位置</small><strong>{{ routeTitle }}</strong></div>
        <div class="topbar-actions">
          <button class="icon-button monthly-button" aria-label="月卡" @click="ui.monthlyCardOpen = true">
            <Ticket :size="18" />
            <i v-if="monthlyBadge" class="inbox-badge unclaimed">{{ monthlyBadge }}</i>
          </button>
          <button class="icon-button achievement-button" aria-label="成就" @click="ui.achievementOpen = true; ui.mailOpen = false">
            <Trophy :size="18" />
            <i v-if="game.state.achievements.claimable" class="inbox-badge unclaimed">{{ game.state.achievements.claimable > 99 ? '99+' : game.state.achievements.claimable }}</i>
          </button>
          <button class="icon-button inbox-button" aria-label="收件箱" @click="ui.mailOpen = true; ui.achievementOpen = false">
            <Inbox :size="18" />
            <i v-if="mailBadge" class="inbox-badge" :class="mailBadge.tone">{{ mailBadge.count > 99 ? '99+' : mailBadge.count }}</i>
          </button>
          <button class="icon-button refresh-button" aria-label="刷新" :class="{ spinning: game.isPending('refresh') }" @click="game.refresh()">
            <RefreshCw :size="18" />
          </button>
        </div>
      </header>

      <div class="status-strip">
        <div class="resource-strip">
          <ResourcePill kind="coins" />
          <ResourcePill kind="stamina" @supply="ui.staminaOpen = true" />
        </div>
        <div class="level-chip" :title="levelHint">
          <span>Lv.{{ game.player.level }}</span>
          <ProgressBar class="level-track" :value="levelProgress" :height="4" track="#27312a" color="linear-gradient(90deg, var(--leaf), var(--gold))" />
          <small>{{ levelHint }}</small>
        </div>
        <ProgressBar class="level-underline" :value="levelProgress" :height="2" track="transparent" color="linear-gradient(90deg, var(--leaf), var(--gold))" />
      </div>

      <div class="page-scroll"><RouterView /></div>
    </div>

    <AppNav variant="bar" @more="ui.navOpen = true" />

    <div v-if="ui.accountOpen" class="modal-backdrop" @click.self="ui.accountOpen = false">
      <section class="account-modal">
        <button class="icon-button modal-close" @click="ui.accountOpen = false"><X :size="20" /></button>
        <div class="account-hero">
          <span class="avatar avatar--large">{{ game.player.display_name.slice(0, 1) }}</span>
          <div>
            <p class="eyebrow">TOWN RESIDENT</p>
            <h2>{{ game.player.display_name }}</h2>
            <span>居民编号 {{ game.player.player_id.slice(0, 8) }}</span>
          </div>
        </div>
        <div class="account-section">
          <h3>QQ Bot 绑定</h3>
          <p>绑定后可以在群聊里查询农场状态。官方 Bot 使用 OpenID，不保存或依赖 QQ 号。</p>
          <p v-if="game.account?.bindings.length" class="bound-chip">已绑定：{{ game.account.bindings.join('、') }}</p>
          <template v-if="bindingCommand">
            <div class="command-box"><code>{{ bindingCommand }}</code><button @click="copyBindingCommand"><Copy :size="16" /></button></div>
            <small>请在 10 分钟内把这条指令发送给机器人。</small>
          </template>
          <ActionButton v-else variant="secondary" action-key="binding-code" @click="generateBindingCode">
            生成 QQ 绑定指令
          </ActionButton>
        </div>
        <button class="logout-button" @click="game.logout(); ui.accountOpen = false">
          <LogOut :size="17" />退出水鱼账号
        </button>
      </section>
    </div>
  </div>

  <MonthlyCardDialog v-if="!isBareRoute" :open="ui.monthlyCardOpen" @close="ui.monthlyCardOpen = false" />
  <StaminaDialog v-if="!isBareRoute" :open="ui.staminaOpen" @close="ui.staminaOpen = false" />
  <MailInbox v-if="!isBareRoute" />
  <AchievementPanel v-if="!isBareRoute" />
  <StoryOverlay v-if="!isBareRoute" />
  <ConfirmDialog />
  <CraftingOutcomeDialog :outcome="game.craftingOutcome" @close="game.craftingOutcome = null" />
  <Transition name="toast"><div v-if="game.notice" class="toast">{{ game.notice }}</div></Transition>
</template>
