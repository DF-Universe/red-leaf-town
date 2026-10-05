<script setup lang="ts">
import { computed, ref } from 'vue'
import { Check, ChevronRight } from 'lucide-vue-next'
import ItemTile from '@/components/ItemTile.vue'
import ModalSheet from '@/components/ModalSheet.vue'
import QualityTag from '@/components/QualityTag.vue'

const props = defineProps<{
  modelValue: string
  label: string
  options: { value: string; label: string; icon?: string; quality?: number; description?: string; disabled?: boolean }[]
  elevated?: boolean
}>()
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()
const open = ref(false)
const selected = computed(() => props.options.find(option => option.value === props.modelValue))
function choose(value: string) {
  emit('update:modelValue', value)
  open.value = false
}
</script>

<template>
  <div class="item-select">
    <span class="select-label">{{ label }}</span>
    <button type="button" class="select-trigger" aria-haspopup="dialog" :aria-expanded="open" :aria-label="`${label}：${selected?.label || '请选择'}`" @click="open = true">
      <ItemTile :icon="selected?.icon" :size="32" tone="plain" />
      <span class="select-copy">
        <QualityTag v-if="selected?.quality" :quality="selected.quality" plain />
        <strong v-else>{{ selected?.label || '请选择' }}</strong>
        <small v-if="selected?.description">{{ selected.description }}</small>
      </span>
      <ChevronRight :size="16" />
    </button>
    <ModalSheet :open="open" :title="`选择${label}`" :elevated="elevated" @close="open = false">
      <div class="select-options">
        <button v-for="option in options" :key="option.value" type="button" class="select-option" :class="{ selected: option.value === modelValue }" :aria-pressed="option.value === modelValue" :disabled="option.disabled" @click="choose(option.value)">
          <ItemTile :icon="option.icon" :size="38" tone="plain" />
          <span class="select-copy">
            <QualityTag v-if="option.quality" :quality="option.quality" plain />
            <strong v-else>{{ option.label }}</strong>
            <small v-if="option.description">{{ option.description }}</small>
          </span>
          <Check v-if="option.value === modelValue" :size="16" class="selected-check" />
        </button>
      </div>
    </ModalSheet>
  </div>
</template>

<style scoped>
.item-select { display: grid; gap: 6px; }
.select-label { color: var(--muted); font-size: 12px; }
.select-trigger, .select-option { display: grid; grid-template-columns: auto minmax(0,1fr) auto; align-items: center; gap: 10px; width: 100%; padding: 9px 10px; text-align: left; border: 1px solid var(--line); border-radius: 12px; background: color-mix(in srgb, var(--bg) 45%, transparent); cursor: pointer; }
.select-trigger { color: var(--muted); }
.select-copy { min-width: 0; }
.select-copy strong { display: block; color: var(--cream); font-size: 13px; font-weight: 500; }
.select-copy small { display: block; margin-top: 3px; color: var(--muted); font-size: 11px; line-height: 1.4; }
.select-options { display: grid; gap: 7px; }
.select-option.selected { border-color: color-mix(in srgb, var(--leaf) 45%, transparent); background: color-mix(in srgb, var(--leaf) 9%, transparent); }
.select-trigger:focus-visible, .select-option:focus-visible { outline: 2px solid var(--leaf-bright); outline-offset: 2px; }
.selected-check { color: var(--leaf-bright); }
</style>
