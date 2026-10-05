from uuid import uuid4

from red_leaf_town.domain.economy import EconomyError, add_item, remove_item, spend_coins
from red_leaf_town.domain.refining import RefiningTask, quality_cap, quality_times, working_seconds


class FacilitiesServiceMixin:
    def _farm_units(self, player, slot):
        plot = self._plot(player, slot)
        return [plot, player.plots[slot + 1]] if slot % 2 == 0 and slot < player.farm_upgrade_stage * 2 else [plot]

    def _feed_upgrade(self, player):
        return next((u for u in self.content.facility_upgrades if u.kind == "feed" and u.stage == player.feed_upgrade_stage), None)

    def _feed_cost_multiplier(self, player):
        upgrade = self._feed_upgrade(player)
        return upgrade.feed_multiplier if upgrade else 1

    def _facility_stage(self, player, kind):
        return {"farm": player.farm_upgrade_stage, "feed": player.feed_upgrade_stage, "refining": int(player.refining.built)}[kind]

    def upgrade_facility(self, subject, upgrade_id, partner_id=""):
        from .service import GameError
        definition = next((u for u in self.content.facility_upgrades if u.id == upgrade_id), None)
        if definition is None:
            raise GameError("facility_not_found", "改良项目不存在", 404)
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            current = self._facility_stage(player, definition.kind)
            if current >= definition.stage:
                return {"changed": False}
            if definition.stage != current + 1 or player.level < definition.min_level:
                raise GameError("facility_locked", "请先完成前一阶段并达到所需等级")
            if definition.kind == "farm":
                slots = player.plots[(definition.stage-1)*2:definition.stage*2]
                if len(slots) != 2 or any(not s.empty for s in slots):
                    raise GameError("farm_not_empty", "两块田均为空时才能合并")
                assigned = [p for s in slots for p in s.assigned_partner_ids]
                if partner_id and partner_id not in assigned:
                    raise GameError("partner_assignment_invalid", "请选择原田块中的驻场伙伴")
                if any(self._partner_lock_deadlines(player, now).get(p, 0) > now for p in assigned):
                    raise GameError("partner_locked", "驻场伙伴还在参与任务")
                slots[0].assigned_partner_ids = [partner_id] if partner_id else []
                slots[1].assigned_partner_ids = []
            try:
                spend_coins(player, definition.coins)
                for entry in definition.inputs:
                    self._consume_minimum_quality_items(player, entry.item_id, entry.quantity, 0)
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            if definition.kind == "farm":
                player.farm_upgrade_stage = definition.stage
            elif definition.kind == "feed":
                player.feed_upgrade_stage = definition.stage
            else:
                player.refining.built = True
                player.refining.settled_at = now
            return {"changed": True, "name": definition.name}

        player, result = self._update_by_sub(subject, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def _settle_refining(self, player, now):
        room = player.refining
        if room.assigned_partner_ids:
            partner_id = room.assigned_partner_ids[0]
            seconds = room.experience_remainders.get(partner_id, 0) + working_seconds(room, now)
            amount, room.experience_remainders[partner_id] = divmod(seconds, 720)
            if amount:
                self._grant_partner_experience(self._owned_partner(player, partner_id), amount)
        room.settled_at = now

    def assign_refining_partner(self, subject, partner_id):
        from .service import GameError
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            room = player.refining
            if not room.built:
                raise GameError("refining_not_built", "请先建造精制房")
            desired = [partner_id] if partner_id else []
            if room.assigned_partner_ids == desired:
                return {"changed": False}
            if room.occupied:
                raise GameError("refining_not_empty", "精制房全部清空后才能换人")
            if partner_id:
                if player.exploration_run and partner_id in player.exploration_run.partner_ids:
                    raise GameError("partner_locked", "伙伴正在探索中")
                self._production_ability(player, [partner_id], "crafting")
                if self._partner_lock_deadlines(player, now).get(partner_id, 0) > now:
                    raise GameError("partner_locked", "伙伴正在参与其他任务")
                self._clear_partner_assignment(player, partner_id, now)
                if player.fishing.companion_partner_id == partner_id:
                    player.fishing.companion_partner_id = ""
            room.assigned_partner_ids = desired
            if self._industry_assigned_count(player, "crafting") > self._industry_partner_capacity(player, "crafting"):
                raise GameError("partner_capacity_reached", "加工与精制的共用编制已满")
            return {"changed": True}

        player, result = self._update_by_sub(subject, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def _refining_abilities(self, player):
        ability = self._production_ability(player, player.refining.assigned_partner_ids, "crafting")[0]
        return ability, ability + int(self._talent_modifier(player, "crafting_quality_bonus"))

    def start_refining(self, subject, slot, recipe_id, quality):
        from .service import GameError
        now = self._now()
        recipe = next((r for r in self.content.refining_recipes if r.id == recipe_id), None)
        if recipe is None or type(quality) is not int or not 1 <= quality <= 4 or not 0 <= slot < 6:
            raise GameError("invalid_refining", "请选择有效的配方、品质与精制格")

        def mutation(player):
            self._settle(player, now)
            room = player.refining
            if not room.built or player.level < recipe.min_level:
                raise GameError("refining_locked", "请先建造精制房并解锁配方")
            if not room.assigned_partner_ids:
                raise GameError("partner_required", "精制需要派驻加工伙伴")
            if room.slots[slot] is not None:
                raise GameError("refining_occupied", "这个精制格已被占用")
            if self._industry_assigned_count(player, "crafting") > self._industry_partner_capacity(player, "crafting"):
                raise GameError("partner_capacity_reached", "加工与精制的共用编制已满")
            ability, quality_ability = self._refining_abilities(player)
            raw = self.content.item_map[recipe.input_item_id]
            product = self.content.item_map[recipe.produce_item_id]
            prices = [self._quality_unit_price(product.sell_price, q) for q in range(1, 5)]
            try:
                remove_item(player, raw.id, recipe.input_quantity, quality)
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            room.slots[slot] = RefiningTask(
                task_id=str(uuid4()), recipe_id=recipe.id, input_item_id=raw.id,
                input_quantity=recipe.input_quantity, input_quality=quality,
                produce_item_id=product.id, started_at=now, ability=ability,
                quality_ability=quality_ability, output_prices=prices,
                input_value=self._quality_unit_price(raw.sell_price, quality) * recipe.input_quantity,
                quality_times=quality_times(now, recipe.top_hours, raw.sell_price * recipe.input_quantity,
                                            prices, ability, quality_ability, quality),
            )
            return {"slot": slot}

        player, result = self._update_by_sub(subject, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def finish_refining(self, subject, slot, task_id, cancel=False):
        from .service import GameError
        now = self._now()
        if not 0 <= slot < 6:
            raise GameError("invalid_slot", "精制格不存在")

        def mutation(player):
            self._settle(player, now)
            room = player.refining
            task = room.slots[slot]
            if task is None or task.task_id != task_id:
                raise GameError("refining_task_changed", "该批次已处理，请刷新后重试", 409)
            quality = task.quality_at(now)
            if not quality and not cancel:
                raise GameError("refining_not_ready", "尚未制成普通品质物产")
            if quality:
                add_item(player, task.produce_item_id, 1, quality)
                counts = player.achievement_stats.refining_batches
                counts[quality] = counts.get(quality, 0) + 1
            else:
                add_item(player, task.input_item_id, task.input_quantity, task.input_quality)
            room.slots[slot] = None
            return {"slot": slot, "quality": quality, "refunded": not bool(quality)}

        player, result = self._update_by_sub(subject, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def _facility_snapshot(self, player, partners, now):
        upgrades = []
        for definition in self.content.facility_upgrades:
            current = self._facility_stage(player, definition.kind)
            if current >= definition.stage:
                continue
            inputs = [{**i.model_dump(), "item": self.content.item_map[i.item_id].model_dump(),
                       "owned": sum(player.inventory.get(i.item_id, {}).values())} for i in definition.inputs]
            upgrades.append({**definition.model_dump(), "inputs": inputs,
                             "unlocked": player.level >= definition.min_level and current + 1 == definition.stage,
                             "affordable": player.coins >= definition.coins and all(i["owned"] >= i["quantity"] for i in inputs)})
        room = player.refining
        ability, quality_ability = self._refining_abilities(player)
        recipes = []
        for recipe in self.content.refining_recipes:
            raw, product = self.content.item_map[recipe.input_item_id], self.content.item_map[recipe.produce_item_id]
            prices = [self._quality_unit_price(product.sell_price, q) for q in range(1, 5)]
            recipes.append({**recipe.model_dump(), "unlocked": player.level >= recipe.min_level,
                            "input_item": raw.model_dump(), "item": product.model_dump(),
                            "options": [{"quality": q, "owned": player.inventory.get(raw.id, {}).get(q, 0),
                                         "input_value": self._quality_unit_price(raw.sell_price, q) * recipe.input_quantity,
                                         "quality_times": quality_times(now, recipe.top_hours, raw.sell_price * recipe.input_quantity,
                                                                        prices, ability, quality_ability, q)} for q in range(1, 5)],
                            "output_prices": prices})
        slots = []
        for task in room.slots:
            if task is None:
                slots.append(None)
            else:
                q = task.quality_at(now)
                slots.append({**task.model_dump(), "quality": q, "max_quality": len(task.quality_times),
                              "next_at": next((at for at in task.quality_times if at > now), None),
                              "item": self.content.item_map[task.produce_item_id].model_dump()})
        return {"upgrades": upgrades, "farm_stage": player.farm_upgrade_stage,
                "feed_stage": player.feed_upgrade_stage,
                "refining": {"built": room.built, "unlocked": player.level >= 20,
                             "assigned_partner": partners.get(room.assigned_partner_ids[0]) if room.assigned_partner_ids else None,
                             "locked": room.occupied, "ability": ability, "quality_ability": quality_ability,
                             "max_quality": quality_cap(quality_ability), "slots": slots, "recipes": recipes}}
