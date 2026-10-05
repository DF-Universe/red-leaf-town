import pytest

from red_leaf_town.achievements import achievement_progress
from red_leaf_town.application import GameError
from red_leaf_town.content import GameContent, load_content
from red_leaf_town.domain import PlayerState, PortalProgressState
from red_leaf_town.domain.economy import add_item
from test_facility_improvements import game, build_room


def test_existing_facility_and_production_records_are_reconciled_once(game):
    service, repo, pid, clock = game
    service.upgrade_facility('facilities', 'farm_1')
    service.upgrade_facility('facilities', 'feed_1')
    service.upgrade_facility('facilities', 'refining_1')
    service.build_pond('facilities', 'pond_blue')
    def history(p):
        p.achievement_stats.harvested_crop_ids.append('grape')
        p.achievement_stats.crafted_recipe_ids.append('make_precision_parts')
    repo.update(pid, history)
    state = service.snapshot_by_sub('facilities')
    completed = {a['achievement_id'] for a in state['achievements']['entries'] if a['completed']}
    assert {'first_double_field', 'feed_upgrade_1', 'refining_room_built', 'blue_pond_built',
            'first_grape_harvest', 'first_precision_parts'} <= completed
    # 库存来自赠送，不能据此伪造领取过矿产或精制的历史。
    assert 'first_blue_iron' not in completed and 'first_refining' not in completed
    claimed = service.claim_all_achievements('facilities')
    again = service.claim_all_achievements('facilities')
    assert not again['result']['claimed']
    assert again['state']['player']['maple_flame'] == claimed['state']['player']['maple_flame']


def test_refining_counts_only_received_batches_and_supports_future_recipes(game):
    service, repo, pid, clock = build_room(game)
    recipe = service.content.refining_recipes[0]
    service.start_refining('facilities', 0, recipe.id, 1)
    task = repo.get(pid).refining.slots[0]
    service.finish_refining('facilities', 0, task.task_id, cancel=True)
    assert not repo.get(pid).achievement_stats.refining_batches
    future = recipe.model_copy(update={'id': 'future_recipe'})
    service.content.refining_recipes.append(future)
    for r in (recipe, future):
        service.start_refining('facilities', 0, r.id, 1)
        task = repo.get(pid).refining.slots[0]
        clock[0] = task.quality_times[-1]
        service.snapshot_by_sub('facilities')  # 制成而未领不能记数。
        before = dict(repo.get(pid).achievement_stats.refining_batches)
        service.finish_refining('facilities', 0, task.task_id, cancel=True)
        after = repo.get(pid).achievement_stats.refining_batches
        assert after[len(task.quality_times)] == before.get(len(task.quality_times), 0) + 1
        with pytest.raises(GameError):
            service.finish_refining('facilities', 0, task.task_id, cancel=True)
        assert repo.get(pid).achievement_stats.refining_batches == after
    state = repo.get(pid)
    total = service.content.achievement_map['refining_batches_30']
    high = service.content.achievement_map['exquisite_refining_20']
    assert achievement_progress(state, total, service.content)[0] == len((recipe, future))
    assert achievement_progress(state, high, service.content)[0] == sum(state.achievement_stats.refining_batches.values())
    assert PlayerState.model_validate_json(state.model_dump_json()).achievement_stats == state.achievement_stats


def test_mining_history_records_task_only_when_collected(game):
    service, repo, pid, clock = game
    task = next(t for t in service.content.mining_tasks if t.produce_item_id == 'blue_iron_ore')
    service.start_mining('facilities', task.site_id, task.id)
    saved = repo.get(pid)
    site = next(s for s in saved.mining_sites if s.site_id == task.site_id)
    clock[0] = site.task_snapshot.ready_at
    service.snapshot_by_sub('facilities')
    assert task.id not in repo.get(pid).achievement_stats.mined_task_ids
    result = service.collect_mining('facilities', task.site_id)
    assert task.id in repo.get(pid).achievement_stats.mined_task_ids
    assert any(a['achievement_id'] == 'first_blue_iron' and a['completed'] for a in result['state']['achievements']['entries'])


def test_third_gate_requires_level_predecessor_quality_and_pays_once(game):
    service, repo, pid, clock = game
    gate = service.content.portal_map['third_gate']
    def predecessors(p):
        p.portals = [PortalProgressState(portal_id=id, completed_at=clock[0]) for id in ('first_gate', 'second_gate')]
    first = gate.tributes[0]
    with pytest.raises(GameError, match='传送门|完成'):
        service.deliver_tribute('facilities', gate.id, first.id, first.quantity)
    repo.update(pid, predecessors)
    repo.update(pid, lambda p: setattr(p, 'experience', service.content.level_definition(gate.min_level - 1).total_xp))
    with pytest.raises(GameError):
        service.deliver_tribute('facilities', gate.id, first.id, first.quantity)
    repo.update(pid, lambda p: setattr(p, 'experience', service.content.level_definition(gate.min_level).total_xp))
    with pytest.raises(GameError):
        service.deliver_tribute('facilities', gate.id, first.id, first.quantity)
    for tribute in gate.tributes:
        repo.update(pid, lambda p: add_item(p, tribute.item_id, tribute.quantity, max(1, tribute.min_quality)))
    before = repo.get(pid)
    partial = service.deliver_tribute('facilities', gate.id, first.id, first.quantity - 1)
    assert not partial['result']['tribute_completed']
    assert repo.get(pid).maple_flame == before.maple_flame
    service.deliver_tribute('facilities', gate.id, first.id, 1)
    for tribute in gate.tributes[1:]:
        service.deliver_tribute('facilities', gate.id, tribute.id, tribute.quantity)
    after = repo.get(pid)
    assert after.maple_flame - before.maple_flame == sum(t.reward.maple_flame for t in gate.tributes)
    assert after.guide_leaves - before.guide_leaves == gate.completion_reward.guide_leaves
    with pytest.raises(GameError):
        service.deliver_tribute('facilities', gate.id, first.id, first.quantity)
    assert repo.get(pid).guide_leaves == after.guide_leaves


@pytest.mark.parametrize('hook,params', [
    ('facility_stage', {'kind': 'farm', 'stage': -1}),
    ('refining_batches', {'count': 1, 'min_quality': 0}),
    ('mining_tasks', {'task_ids': ['missing']}),
])
def test_new_achievement_hooks_reject_invalid_configuration(hook, params):
    payload = load_content().model_dump()
    payload['achievements'][0]['condition'] = {'hook': hook, 'params': params}
    with pytest.raises(ValueError):
        GameContent.model_validate(payload)
