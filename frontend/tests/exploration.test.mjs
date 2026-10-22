import assert from 'node:assert/strict'
import test from 'node:test'
import { explorationWithdrawalText } from '../src/lib/exploration.ts'

const base = { expedition_id: 'midsummer_appeal_ground', completed: true, depth: 2, stamina_spent: 9, entry_fee: 0, drops: [], equipment_drops: [], returned_items: [], guide_leaves: 0 }
const item = (id, name, quantity, quality = 0) => ({ item_id: id, item: { name }, quantity, quality })

test('fixed seed rewards and first-clear leaves are displayed without a top-level quantity', () => {
  const result = { ...base, equipment_drops: [item('ribbed_berry_seed', '棱瓜果种子', 2)], guide_leaves: 2 }
  assert.equal(explorationWithdrawalText(result), '返程带回了棱瓜果种子×2、引路枫叶×2')
})

test('mixed rewards retain quality and include equipment', () => {
  const result = { ...base, drops: [item('red_copper_ore', '赤铜矿', 3, 2)], equipment_drops: [item('moonlit_pendant', '月光吊坠', 1)] }
  assert.equal(explorationWithdrawalText(result), '返程带回了良品赤铜矿×3、月光吊坠×1')
})

test('unused carried items are distinguished from rewards', () => {
  const result = { ...base, returned_items: [item('herbal_salve', '草药膏', 1, 2)] }
  assert.equal(explorationWithdrawalText(result), '从第 2 段路线返程，本次没有战利品；退回未使用的良品草药膏×1')
})

test('empty early withdrawal never interpolates an undefined quantity', () => {
  assert.equal(explorationWithdrawalText({ ...base, completed: false, depth: 0 }), '从第 0 段路线返程，本次没有战利品')
})
