<script setup lang="ts">
import { Sparkles } from 'lucide-vue-next'

withDefaults(
  defineProps<{
    title?: string
    backgroundUrl?: string | null
    pityTags?: string[]
  }>(),
  { pityTags: () => [] },
)
</script>

<template>
  <article class="gacha-poster" :class="{ 'has-meta': !!$slots.meta }">
    <div class="gacha-poster-media" :style="backgroundUrl ? { backgroundImage: `url(${backgroundUrl})` } : undefined">
      <div v-if="!backgroundUrl" class="gacha-poster-placeholder"><Sparkles :size="48" /></div>
    </div>
    <div class="gacha-poster-scrim" />
    <div class="gacha-poster-frame" />

    <div class="gacha-poster-copy">
      <h2 v-if="title">{{ title }}</h2>
      <div v-if="pityTags.length" class="gacha-poster-pity">
        <span v-for="tag in pityTags" :key="tag">{{ tag }}</span>
      </div>
    </div>

    <!-- 卡面左下角的补充信息（轮替池用）：挂在外层，定位才相对整张卡面 -->
    <div v-if="$slots.meta" class="gacha-poster-meta"><slot name="meta" /></div>

    <div v-if="$slots.actions" class="gacha-poster-glass"><slot name="actions" /></div>
  </article>
</template>

<style scoped>
.gacha-poster {
  position: relative;
  overflow: hidden;
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 1px solid #d8b76b33;
  border-radius: 34px 10px 34px 10px;
  background: linear-gradient(135deg, #1f2c23, #111914);
}
.gacha-poster-media {
  position: absolute;
  inset: 0;
  background-position: center 22%;
  background-size: cover;
  filter: saturate(1.05);
}
.gacha-poster-placeholder { position: absolute; inset: 0; display: grid; place-items: center; color: #3a4a3e; }
.gacha-poster-scrim {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(to top, rgba(9, 13, 10, .96) 0%, rgba(9, 13, 10, .68) 34%, rgba(9, 13, 10, .12) 62%, transparent 82%),
    linear-gradient(100deg, rgba(9, 13, 10, .46) 0%, rgba(9, 13, 10, .18) 26%, transparent 48%),
    radial-gradient(circle at 82% 12%, rgba(215, 173, 88, .22), transparent 40%);
}
.gacha-poster-frame {
  position: absolute;
  inset: 10px;
  pointer-events: none;
  border: 1px solid rgba(244, 238, 223, .14);
  border-radius: 26px 8px 26px 8px;
}

/* 卡面左下角带补充信息时，底部压得更深一些，保证小字读得清 */
.gacha-poster.has-meta .gacha-poster-scrim {
  background:
    linear-gradient(to top, rgba(9, 13, 10, .99) 0%, rgba(9, 13, 10, .88) 22%, rgba(9, 13, 10, .5) 44%, rgba(9, 13, 10, .12) 66%, transparent 86%),
    linear-gradient(100deg, rgba(9, 13, 10, .46) 0%, rgba(9, 13, 10, .18) 26%, transparent 48%),
    radial-gradient(circle at 82% 12%, rgba(215, 173, 88, .22), transparent 40%);
}
.gacha-poster-copy { position: relative; z-index: 1; max-width: min(70%, 520px); padding: clamp(20px, 4vw, 40px); }
.gacha-poster-copy h2 {
  max-width: 460px;
  margin: 0 0 14px;
  color: #f8f2df;
  font: 700 clamp(1.8rem, 4.4vw, 3.2rem) Georgia, 'Noto Serif SC', serif;
  -webkit-text-stroke: 1.6px #000;
  paint-order: stroke fill;
  text-shadow: 0 3px 12px rgba(0, 0, 0, .4);
}
.gacha-poster-meta {
  position: absolute;
  left: clamp(16px, 2.6vw, 30px);
  bottom: clamp(16px, 2.6vw, 28px);
  z-index: 2;
  width: min(56%, 440px);
}
.gacha-poster-meta:empty { display: none; }
.gacha-poster-pity { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.gacha-poster-pity span { padding: 5px 9px; color: #f0dfa8; font-size: 11px; font-weight: 600; border-radius: 99px; background: #3a2e14e0; border: 1px solid #d7b46b8c; }

.gacha-poster-glass {
  position: absolute;
  right: clamp(12px, 2.4vw, 22px);
  bottom: clamp(12px, 2.4vw, 22px);
  z-index: 2;
  display: grid;
  gap: 8px;
  min-width: 210px;
  padding: 15px;
  border: 1px solid rgba(255, 255, 255, .16);
  border-radius: 18px 6px 18px 6px;
  background: rgba(19, 27, 22, .5);
  backdrop-filter: blur(16px) saturate(140%);
  -webkit-backdrop-filter: blur(16px) saturate(140%);
  box-shadow: 0 20px 50px rgba(0, 0, 0, .35);
}

@media (max-width: 720px) {
  .gacha-poster { display: grid; grid-template-columns: minmax(0, 1fr); aspect-ratio: auto; border-radius: 22px 8px; }
  .gacha-poster-media { position: relative; inset: auto; grid-area: 1 / 1; width: 100%; aspect-ratio: 16 / 9; background-position: center; }
  .gacha-poster-scrim { position: relative; inset: auto; grid-area: 1 / 1; background: linear-gradient(to top, #09100ee6, #09100e00 65%); pointer-events: none; }
  .gacha-poster-frame { display: none; }
  .gacha-poster-copy { display: contents; }
  .gacha-poster-copy h2 { grid-area: 1 / 1; align-self: end; z-index: 1; min-width: 0; margin: 14px; font-size: clamp(24px, 6vw, 34px); overflow-wrap: anywhere; }
  .gacha-poster-meta { position: static; grid-area: 2 / 1; width: auto; padding: 0 14px 12px; z-index: 1; }
  .gacha-poster-pity { grid-area: 3 / 1; margin: 0; padding: 12px 14px; gap: 6px; background: #17221c; }
  .gacha-poster-pity span { max-width: 100%; line-height: 1.6; overflow-wrap: anywhere; }
  .gacha-poster-glass { position: static; grid-area: 4 / 1; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: center; min-width: 0; padding: 12px 14px 14px; gap: 10px 8px; border: 0; border-top: 1px solid var(--line); border-radius: 0; background: #1b2921; backdrop-filter: none; box-shadow: none; }
}
</style>
