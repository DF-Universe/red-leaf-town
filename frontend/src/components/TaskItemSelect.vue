<script setup lang="ts">
import { computed, ref } from 'vue'
import { Ban, Check, ChevronRight } from 'lucide-vue-next'

import ItemTile from '@/components/ItemTile.vue'
import ModalSheet from '@/components/ModalSheet.vue'
import { useGameStore } from '@/stores/game'

const props = withDefaults(defineProps<{
  modelValue: string
  industry: string
  quantity?: number
  timing?: 'start' | 'active'
  /* 在别的弹窗里打开时抬高层级，避免被父弹窗盖住 */
  elevated?: boolean
}>(), { timing: 'start', elevated: false, quantity: 1 })
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()
const game = useGameStore()

const options = computed(() => (game.state?.task_items || []).filter((item) => (
  item.timing === props.timing
  && (!item.eligible_industries.length || item.eligible_industries.includes(props.industry))
)))
const selected = computed(() => options.value.find((item) => item.id === props.modelValue) || null)
const open = ref(false)

function choose(id: string) {
  emit('update:modelValue', id)
  open.value = false
}
</script>

<template>
  <!-- 外层 .stop：这个选择器常嵌在「开工」按钮内部，点它不应该顺手把任务开了 -->
  <div v-if="options.length" class="task-item-select" @click.stop>
    <div
      class="task-item-trigger"
      :class="{ chosen: Boolean(selected) }"
      role="button"
      tabindex="0"
      @click="open = true"
      @keydown.enter.prevent="open = true"
      @keydown.space.prevent="open = true"
    >
      <ItemTile :icon="selected?.icon" :size="24" :icon-size="14" :tone="selected ? 'gold' : 'plain'" />
      <span class="task-item-label">
        <strong>{{ selected ? selected.name : '不使用特殊道具' }}</strong>
        <small v-if="selected">×{{ selected.quantity }} · {{ selected.description }}</small>
      </span>
      <ChevronRight :size="15" class="task-item-caret" />
    </div>

    <ModalSheet
      :open="open"
      title="特殊道具"
      :subtitle="`本次开工消耗 ${quantity} 份道具，每份产出各使用一份`"
      :elevated="elevated"
      @close="open = false"
    >
      <div class="task-item-list">
        <button type="button" class="task-item-option" :class="{ current: !modelValue }" @click="choose('')">
          <ItemTile :size="42" tone="plain"><Ban :size="19" /></ItemTile>
          <span class="task-item-copy">
            <strong>不使用</strong>
            <small>本次任务不带道具，按常规产出结算。</small>
          </span>
          <Check v-if="!modelValue" :size="16" class="task-item-check" />
        </button>

        <button
          v-for="item in options"
          :key="item.id"
          type="button"
          class="task-item-option"
          :class="{ current: item.id === modelValue }"
          :disabled="item.quantity < quantity"
          @click="choose(item.id)"
        >
          <ItemTile :icon="item.icon" :size="42" :icon-size="21" tone="gold" />
          <span class="task-item-copy">
            <strong>{{ item.name }}<i>×{{ item.quantity }}</i></strong>
            <small>{{ item.description }}</small>
          </span>
          <Check v-if="item.id === modelValue" :size="16" class="task-item-check" />
        </button>
      </div>
    </ModalSheet>
  </div>
</template>

<style scoped>
.task-item-select { margin-top: 7px; }
.task-item-trigger {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 5px 8px;
  color: #8c998f;
  border: 1px dashed #ffffff14;
  border-radius: 9px;
  background: #0d141086;
  cursor: pointer;
  transition: border-color .16s ease, background .16s ease;
}
.task-item-trigger:hover { background: #ffffff0b; }
.task-item-trigger:focus-visible { outline: 1px solid var(--leaf-bright); outline-offset: 1px; }
.task-item-trigger.chosen { color: #d9e2d6; border-style: solid; border-color: #d7ad5840; background: #d7ad580f; }
.task-item-label { min-width: 0; }
.task-item-label strong { display: block; font-size: 12px; font-weight: 700; }
.task-item-label small { display: block; margin-top: 1px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #7f8b80; font-size: 11px; }
.task-item-caret { flex: 0 0 auto; color: #7b867d; }

.task-item-list { display: grid; gap: 7px; }
.task-item-option {
  width: 100%;
  min-height: 62px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  text-align: left;
  color: #d9e1d7;
  border: 1px solid transparent;
  border-radius: 13px;
  background: #ffffff05;
  cursor: pointer;
}
.task-item-option:hover { background: #ffffff0b; }
.task-item-option.current { border-color: color-mix(in srgb, var(--gold) 45%, transparent); background: color-mix(in srgb, var(--gold) 10%, transparent); }
.task-item-copy { min-width: 0; }
.task-item-copy strong { display: block; font-size: 14px; }
.task-item-copy strong i { margin-left: 7px; color: var(--gold); font-size: 12px; font-style: normal; }
.task-item-copy small { display: block; margin-top: 3px; color: #93a094; font-size: 12px; line-height: 1.55; }
.task-item-check { color: var(--leaf-bright); }
</style>
