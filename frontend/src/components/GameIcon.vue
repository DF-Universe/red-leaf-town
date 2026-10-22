<script setup lang="ts">
import { Sun, Anvil, Settings, Wine, Bean, Beef, Bird, BookOpen, Bug, Carrot, Cherry, Citrus, CookingPot, Crown, Droplet, Droplets, Egg, EggFried, Fish, FishSymbol, FlaskConical, Flower2, Footprints, Gem, GlassWater, Grape, LampDesk, Leaf, Mic, Milk, Moon, Package, PackageCheck, PanelsTopLeft, Pickaxe, Rat, Repeat2, Shell, Shield, ShieldAlert, Sprout, Sword, Swords, Target, Ticket, Trees, Vegan, Waves, Wheat, Wind } from 'lucide-vue-next'
import { computed } from 'vue'

const props = defineProps<{ name?: string; size?: number }>()
const icons = { sun: Sun, anvil: Anvil, settings: Settings, wine: Wine, bean: Bean, beef: Beef, bird: Bird, 'book-open': BookOpen, bug: Bug, crown: Crown, footprints: Footprints, moon: Moon, rat: Rat, shield: Shield, 'shield-alert': ShieldAlert, spider: Bug, sword: Sword, swords: Swords, target: Target, carrot: Carrot, cherry: Cherry, citrus: Citrus, 'cooking-pot': CookingPot, droplet: Droplet, droplets: Droplets, fish: Fish, 'fish-symbol': FishSymbol, egg: Egg, 'egg-fried': EggFried, 'flask-conical': FlaskConical, 'flower-2': Flower2, gem: Gem, 'glass-water': GlassWater, grape: Grape, 'lamp-desk': LampDesk, leaf: Leaf, mic: Mic, milk: Milk, package: Package, 'package-check': PackageCheck, 'panels-top-left': PanelsTopLeft, pickaxe: Pickaxe, 'repeat-2': Repeat2, shell: Shell, sprout: Sprout, ticket: Ticket, trees: Trees, vegan: Vegan, waves: Waves, wheat: Wheat, wind: Wind }
const component = computed(() => icons[props.name as keyof typeof icons] || Package)
</script>

<template>
  <component :is="component" :size="size || 24" stroke-width="1.8" />
</template>
