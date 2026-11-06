from uuid import uuid4
from collections import Counter
from math import expm1, log1p

from red_leaf_town.domain.economy import EconomyError, add_item, remove_item, spend_coins
from red_leaf_town.domain.progression import consume_stamina, grant_experience, refund_stamina
from red_leaf_town.domain.sailing import SailingDrop, SailingLog, SailingRun
from red_leaf_town.domain.models import TaskOutputSnapshot
from red_leaf_town.domain.production import draw_count, draw_weighted_batches
from red_leaf_town.domain.quality import QUALITY_NAMES, quality_probabilities, roll_quality
from red_leaf_town.sailing_content import load_sailing_content
from red_leaf_town.partner_content import level_cap_for_breakthrough


def sailing_error(code, message, status=400):
    from .service import GameError
    return GameError(code, message, status)


def sailing_output_pool(route, draws, stamina, nets_level=0, rare_supply=False):
    """Use the exploration weighted pool; calibrate equipment to first-acquisition stamina."""
    pool = [TaskOutputSnapshot(
        item_id=o.item_id, quantity_min=o.quantity_min, quantity_max=o.quantity_max,
        weight=o.weight * (1 + nets_level * 0.4 + (0.75 if rare_supply else 0)
                           if o.rarity == 'rare' else 1),
    ) for o in route.outputs]
    equipment_id = next(o.item_id for o in route.outputs if o.rarity == 'equipment')
    other_weight = sum(o.weight for o in pool if o.item_id != equipment_id)
    probability = -expm1(log1p(-stamina / route.equipment_expected_stamina) / draws)
    for output in pool:
        if output.item_id == equipment_id:
            output.weight = other_weight * probability / (1 - probability)
    return pool


class SailingServiceMixin:
    def build_sailing_ship(self, oauth_sub):
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            content = self._require_sailing(player)
            if player.sailing.ship_built:
                return {'duplicate': True}
            try:
                spend_coins(player, content.construction_coins)
                for material in content.construction_materials:
                    self._consume_any_quality(player, material.item_id, material.quantity)
            except EconomyError as exc:
                raise sailing_error('resource_insufficient', str(exc)) from exc
            player.sailing.ship_built = True
            return {'ship_built': True}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {'result': result, 'state': self._snapshot(player, now)}

    def _require_sailing(self, player):
        content = load_sailing_content()
        content.validate_items(self.content.item_map)
        if player.level < content.min_level:
            raise sailing_error('content_locked', f'居民达到 {content.min_level} 级后可以出海', 403)
        return content

    def start_sailing(self, oauth_sub, route_id, partner_ids, supply_id='none', request_id=''):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise sailing_error('request_invalid', '缺少有效的出航请求编号')
        if (not isinstance(partner_ids, list) or not 1 <= len(partner_ids) <= 3
                or any(not isinstance(p, str) or not p for p in partner_ids)
                or len(set(partner_ids)) != len(partner_ids)):
            raise sailing_error('sailing_party_invalid', '请选择一至三名不同伙伴')
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            content = self._require_sailing(player)
            state = player.sailing
            if request_id in state.start_requests:
                return {'duplicate': True}
            if not state.ship_built:
                raise sailing_error('sailing_ship_required', '请先建造初帆号')
            if state.active_run:
                raise sailing_error('sailing_active', '船还在航行或等待领取，请先领取上次收获', 409)
            route = next((r for r in content.routes if r.id == route_id), None)
            supply = next((s for s in content.supplies if s.id == supply_id), None)
            if route is None or supply is None:
                raise sailing_error('sailing_invalid', '航线或补给不存在', 404)
            if state.completed_voyages < route.required_voyages:
                raise sailing_error('content_locked', f'完成 {route.required_voyages} 次航行后开放这条航线')
            catalog = self.partner_catalog_loader().partner_map
            owned = {p.partner_id: p for p in player.owned_partners}
            locks = self._partner_lock_deadlines(player, now)
            for partner_id in partner_ids:
                if partner_id not in owned or partner_id not in catalog:
                    raise sailing_error('partner_not_owned', '队伍中有尚未持有或无法使用的伙伴', 404)
                if locks.get(partner_id, 0) > now:
                    raise sailing_error('partner_locked', '伙伴正在参与任务，暂时不能出海', 409)
                if player.exploration_run and partner_id in player.exploration_run.partner_ids:
                    raise sailing_error('partner_locked', '伙伴正在探索，暂时不能出海', 409)
            for partner_id in partner_ids:
                self._clear_partner_assignment(player, partner_id, now)
                if player.fishing.companion_partner_id == partner_id:
                    player.fishing.companion_partner_id = ""
            trial = state.completed_voyages == 0
            duration, coins, stamina = (60, 20, 1) if trial else (route.duration, route.coins, route.stamina)
            consumed = []
            try:
                spend_coins(player, coins)
                consume_stamina(player, stamina, self.content, now)
                remaining = supply.quantity
                for quality, quantity in sorted(player.inventory.get(supply.item_id, {}).items()):
                    count = min(remaining, quantity)
                    if count:
                        remove_item(player, supply.item_id, count, quality)
                        consumed.append({'item_id': supply.item_id, 'quality': quality, 'quantity': count})
                        remaining -= count
                    if not remaining:
                        break
                if remaining:
                    raise EconomyError('额外补给数量不足')
            except (EconomyError, ValueError) as exc:
                raise sailing_error('resource_insufficient', str(exc)) from exc
            industry = 'exploration' if route.required_voyages >= 3 else 'aquatic'
            abilities = []
            for partner_id in partner_ids:
                member = owned[partner_id]
                definition = catalog[partner_id]
                level = min(member.level, level_cap_for_breakthrough(member.breakthrough), self.content.industries[industry].partner_level_cap)
                abilities.append(definition.ability_at(industry, level, member.stars)
                                 if any(t.industry == industry for t in definition.tendencies) else 0)
            ability = round(max(abilities) + (sum(abilities) - max(abilities)) * 0.25)
            ability += self.content.industries[industry].character_base_ability + self._industry_ability_bonus(player, industry)
            trait_context = {"industry": industry, "event_check_bonus": 0, "star_guidance_partner_id": "", "applied_effects": []}
            self._execute_partner_trait_phase(partner_ids, "sailing_prepare", trait_context, player=player)
            rescue_available = bool(trait_context["star_guidance_partner_id"])
            base = 1 if trial else route.draws.base_draws
            quantity = draw_count(ability, base, route.draws.ability_bonus, route.draws.difficulty)
            quantity += round(base * (state.cargo_level * 0.1 + (0.2 if supply.effect == 'quantity' else 0)))
            bonus = 0
            logs = []
            for event in self.rng.sample(content.events, 1 if trial else route.events):
                actor = max(partner_ids, key=lambda p: catalog[p].exploration_stats.modifier(event.attribute))
                modifier = catalog[actor].exploration_stats.modifier(event.attribute) + int(trait_context["event_check_bonus"])
                roll = self.rng.randint(1, 20)
                success = roll == 20 or (roll != 1 and roll + modifier >= 12)
                initial_roll = None
                rerolls = []
                rescue_id = ""
                rescue_bonus = 0
                if not success and rescue_available:
                    rescue_available = False
                    rescue_id = trait_context["star_guidance_partner_id"]
                    initial_roll = roll
                    rerolls = [self.rng.randint(1, 20), self.rng.randint(1, 20)]
                    roll = max(rerolls)
                    success = roll == 20 or (roll != 1 and roll + modifier >= 12)
                    rescue_bonus = 1 if success else 0
                text = event.success if success else event.failure
                if success:
                    bonus += max(1, base // 8)
                elif event.id == 'squall':
                    if supply.effect == 'protect':
                        text = '伙伴用维修物资加固货舱，额外收获也安然无恙。'
                    else:
                        bonus = max(0, bonus - max(1, base // 8))
                if rescue_id:
                    text = (f"{catalog[rescue_id].name}循星引航：原骰 {initial_roll}，优势重掷 {rerolls[0]}／{rerolls[1]}，取 {roll}。"
                            + text + (" 额外获得一次物产抽取。" if rescue_bonus else ""))
                logs.append(SailingLog(initial_roll=initial_roll, rerolls=rerolls,
                                       rescue_partner_id=rescue_id, rescue_bonus_draws=rescue_bonus, event_id=event.id, name=event.name, text=text, success=success,
                                       roll=roll, modifier=modifier, attribute=event.attribute, actor_id=actor))
            count = quantity + bonus + sum(log.rescue_bonus_draws for log in logs)
            pool = sailing_output_pool(route, count, stamina, state.nets_level, supply.effect == 'rare')
            totals = Counter()
            probabilities = {
                o.item_id: quality_probabilities(ability, o.quality.thresholds, o.quality.width,
                                                o.quality.miracle_probability_cap, o.quality.miracle_eligible)
                for o in route.outputs if o.quality is not None
            }
            for item_id, amount in draw_weighted_batches(self.rng, pool, count):
                for _ in range(amount):
                    quality = roll_quality(self.rng, probabilities[item_id]) if item_id in probabilities else 0
                    totals[item_id, quality] += 1
            drops = [SailingDrop(item_id=item_id, quality=quality, quantity=amount)
                     for (item_id, quality), amount in totals.items()]
            state.active_run = SailingRun(
                rule_version=4,
                run_id=str(uuid4()), route_id=route.id, route_name=route.name, partner_ids=partner_ids,
                started_at=now, ready_at=now + duration, trial=trial, supply_id=supply.id, coins=coins,
                stamina=stamina, experience=stamina * 6, partner_experience=max(1, duration // 720 + stamina * 2),
                ability=ability, cargo_level=state.cargo_level, nets_level=state.nets_level,
                consumed_inputs=consumed, drops=drops, logs=logs, applied_effects=trait_context["applied_effects"],
            )
            state.start_requests = [*state.start_requests[-49:], request_id]
            return {'run_id': state.active_run.run_id, 'trial': trial}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {'result': result, 'state': self._snapshot(player, now)}

    def cancel_sailing(self, oauth_sub, run_id):
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            self._require_sailing(player)
            run = player.sailing.active_run
            if not run or run.run_id != run_id:
                raise sailing_error('sailing_not_found', '没有这次进行中的航行', 404)
            if now >= run.ready_at:
                raise sailing_error('sailing_already_arrived', '船已回港，请领取航海收获', 409)
            player.coins += run.coins
            refund_stamina(player, run.stamina, self.content, now)
            for item in run.consumed_inputs:
                add_item(player, item['item_id'], item['quantity'], item['quality'])
            player.sailing.active_run = None
            return {'run_id': run.run_id, 'refunded_coins': run.coins,
                    'returned_items': run.consumed_inputs}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {'result': result, 'state': self._snapshot(player, now)}

    def collect_sailing(self, oauth_sub, run_id):
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            self._require_sailing(player)
            state = player.sailing
            if state.last_run and state.last_run.run_id == run_id:
                return {'duplicate': True}
            run = state.active_run
            if not run or run.run_id != run_id:
                raise sailing_error('sailing_not_found', '没有这次待领取的航行', 404)
            if now < run.ready_at:
                raise sailing_error('sailing_not_ready', '船还没有回港', 409)
            for drop in run.drops:
                quality = drop.quality or (1 if self.content.item_map[drop.item_id].has_quality else 0)
                add_item(player, drop.item_id, drop.quantity, quality)
                state.collected_items[drop.item_id] = state.collected_items.get(drop.item_id, 0) + drop.quantity
            grant_experience(player, run.experience, self.content, max_level=self._player_level_cap(player))
            for partner_id in run.partner_ids:
                self._grant_partner_experience(self._owned_partner(player, partner_id), run.partner_experience)
            state.discoveries = sorted(set(state.discoveries) | {log.event_id for log in run.logs if log.success})
            state.completed_voyages += 1
            player.achievement_stats.sailing_route_ids = sorted(
                set(player.achievement_stats.sailing_route_ids) | {run.route_id}
                | ({state.last_run.route_id} if state.last_run else set())
            )
            state.last_run = run
            state.active_run = None
            return {'run_id': run.run_id, 'drops': self._sailing_drops(run.drops), 'experience': run.experience}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {'result': result, 'state': self._snapshot(player, now)}

    def upgrade_sailing(self, oauth_sub, kind, expected_level):
        if kind not in ('cargo', 'nets') or type(expected_level) is not int:
            raise sailing_error('sailing_upgrade_invalid', '改装请求无效')
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            self._require_sailing(player)
            state = player.sailing
            if not state.ship_built:
                raise sailing_error('sailing_ship_required', '请先建造初帆号')
            if state.active_run:
                raise sailing_error('sailing_active', '请回港领取收获后再改装', 409)
            level = getattr(state, f'{kind}_level')
            if level != expected_level or level >= 3:
                raise sailing_error('sailing_upgrade_invalid', '改装等级已变化或已经满级', 409)
            item_id = 'maple_plank' if kind == 'cargo' else 'red_copper_ore'
            try:
                spend_coins(player, (level + 1) * 1000)
                remaining = (level + 1) * 5
                for quality, quantity in sorted(player.inventory.get(item_id, {}).items()):
                    count = min(remaining, quantity)
                    if count:
                        remove_item(player, item_id, count, quality)
                        remaining -= count
                    if not remaining:
                        break
                if remaining:
                    raise EconomyError('改装材料不足')
            except (EconomyError, ValueError) as exc:
                raise sailing_error('resource_insufficient', str(exc)) from exc
            setattr(state, f'{kind}_level', level + 1)
            return {'kind': kind, 'level': level + 1}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {'result': result, 'state': self._snapshot(player, now)}

    def _sailing_drops(self, drops):
        items = self.content.item_map
        return [{**d.model_dump(), 'quality_name': QUALITY_NAMES.get(d.quality, ''),
                 'name': items[d.item_id].name if d.item_id in items else d.item_id}
                for d in drops]

    def _sailing_snapshot(self, player, now):
        content = load_sailing_content()
        state = player.sailing

        def run_snapshot(run):
            if run is None:
                return None
            ready = now >= run.ready_at
            return {**run.model_dump(exclude={'drops', 'logs', 'consumed_inputs'}), 'ready': ready,
                    'drops': self._sailing_drops(run.drops) if ready else [],
                    'logs': [log.model_dump() for log in run.logs] if ready else []}

        trial = state.completed_voyages == 0
        items = self.content.item_map
        return {
            'unlocked': player.level >= content.min_level,
            'min_level': content.min_level, 'trial_available': trial,
            'ship_built': state.ship_built,
            'construction': {
                'coins': content.construction_coins,
                'materials': [
                    {**m.model_dump(), 'item_name': items[m.item_id].name,
                     'owned': sum(player.inventory.get(m.item_id, {}).values())}
                    for m in content.construction_materials
                ],
            },
            'completed_voyages': state.completed_voyages,
            'routes': [{**r.model_dump(), 'duration': 60 if trial else r.duration,
                        'coins': 20 if trial else r.coins, 'stamina': 1 if trial else r.stamina,
                        'unlocked': player.level >= content.min_level and state.completed_voyages >= r.required_voyages,
                        'outputs': [{**o.model_dump(), 'name': items[o.item_id].name}
                                    for o in r.outputs]}
                       for r in content.routes],
            'supplies': [{**s.model_dump(), 'owned': sum(player.inventory.get(s.item_id, {}).values()),
                          'item_name': items[s.item_id].name if s.item_id else ''} for s in content.supplies],
            'upgrades': [{'kind': kind, 'name': name, 'level': getattr(state, f'{kind}_level'),
                          'coins': (getattr(state, f'{kind}_level') + 1) * 1000,
                          'quantity': (getattr(state, f'{kind}_level') + 1) * 5,
                          'item_id': item_id, 'item_name': items[item_id].name,
                          'owned': sum(player.inventory.get(item_id, {}).values()), 'description': description}
                         for kind, name, item_id, description in (
                             ('cargo', '货舱', 'maple_plank', '每级增加基础抽取次数的 10%'),
                             ('nets', '渔具', 'red_copper_ore', '每级增加 40% 稀有海产权重'))],
            'active_run': run_snapshot(state.active_run), 'last_run': run_snapshot(state.last_run),
            'discoveries': [{'id': e.id, 'name': e.name if e.id in state.discoveries else '未知见闻',
                             'discovered': e.id in state.discoveries} for e in content.events],
            'collection': [{'item_id': item_id, 'name': items[item_id].name if count else '未知物产', 'quantity': count}
                           for item_id in sorted({o.item_id for r in content.routes for o in r.outputs})
                           for count in [state.collected_items.get(item_id, 0)]],
        }
