import random
from math import ceil

import pytest

from red_leaf_town.application import GameError
from red_leaf_town.domain import DelveMemberState, ExplorationRunState
from red_leaf_town.domain import delve_battle
from red_leaf_town.domain.economy import add_item
from red_leaf_town.domain.sailing import SailingRun
from red_leaf_town.partner_content import load_partner_catalog, PartnerCatalog
from test_livestock import ranch, set_level, give
from test_delve_battle import member, battle, FixedRandom
from test_delve_run import delve_game, start as start_delve
from test_sailing import sailing_game, start as start_sailing


class ZeroRandom(random.Random):
    def random(self): return 0.0


@pytest.mark.parametrize('industry', ['crafting', 'exploration'])
def test_public_talent_paths_require_prerequisites(ranch, monkeypatch, industry):
    service, repo, _, player = ranch
    nodes = [n for n in service.content.talents if n.industry == industry]
    assert nodes
    assert all(n.cost == 1 for n in nodes)
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS','someone-else')
    assert sum(n['industry'] == industry for n in service.snapshot_by_sub('stock-sub')['talents']['nodes']) == len(nodes)
    set_level(repo,player.player_id,20)
    with pytest.raises(GameError, match='前置'):
        service.unlock_talent('stock-sub',nodes[-1].id)
    service.snapshot_by_sub("stock-sub")
    points_before = service._available_talent_points(repo.get(player.player_id))
    for n in nodes: service.unlock_talent('stock-sub',n.id)
    assert service._available_talent_points(repo.get(player.player_id)) == points_before - sum(n.cost for n in nodes)


def test_crafting_freezes_quality_speed_refund_and_refunds_once_per_recipe(ranch, monkeypatch):
    service,repo,clock,player=ranch
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS',player.player_id)
    set_level(repo,player.player_id,20)
    for n in service.content.talents:
        if n.industry=='crafting':service.unlock_talent('stock-sub',n.id)
    give(repo,player.player_id,'flax',6,1)
    repo.update(player.player_id,lambda p:setattr(p,'stamina',40))
    service.start_crafting('stock-sub','town_workbench','make_flax_thread',quantity=2)
    saved=repo.get(player.player_id)
    task=saved.crafting_stations[0].task_snapshot
    assert task.quality_parameters.ability==35
    assert task.final_duration==ceil(ceil(120/(1+2*20/(20+130)))*.9)
    # Removing talents after departure does not change the frozen task.
    repo.update(player.player_id,lambda p:setattr(p,'talent_nodes',[]))
    service.rng=ZeroRandom(1)
    clock.advance(240)
    result=service.collect_crafting('stock-sub','town_workbench')['result']
    assert result['quantity']==6 and result['refunded_stamina']==2
    assert result['experience']==24
    assert repo.get(player.player_id).stamina==38
    with pytest.raises(GameError):service.collect_crafting('stock-sub','town_workbench')
    assert repo.get(player.player_id).stamina==38


def test_cancelled_crafting_never_earns_talent_refund(ranch,monkeypatch):
    service,repo,clock,player=ranch
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS',player.player_id)
    set_level(repo,player.player_id,20)
    def prepare(p):
        p.talent_nodes=['crafting_refund_1'];p.stamina=40
        add_item(p,'flax',6,1)
    repo.update(player.player_id,prepare)
    service.rng=ZeroRandom(1)
    service.start_crafting('stock-sub','town_workbench','make_flax_thread',quantity=2)
    service.cancel_task('stock-sub','crafting','town_workbench')
    assert repo.get(player.player_id).stamina==38  # Only queued work refunded, active work consumed.


def test_exploration_talents_are_frozen_for_the_whole_run(delve_game,monkeypatch):
    service,repo,player=delve_game
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS',player.player_id)
    def prepare(p):
        p.experience=service.content.level_definition(20).total_xp
        p.talent_nodes=[n.id for n in service.content.talents if n.industry=='exploration']
    repo.update(player.player_id,prepare)
    start_delve(service)
    run=repo.get(player.player_id).exploration_run
    assert run.talent_check_bonus==1
    assert run.exploration_ability==65 # leader 40, support 20*25%, talents 20
    for id,m in run.combat_party.items():
        stats=service.partner_catalog_loader().partner_map[id].exploration_stats
        assert m.max_hp==delve_battle.member_max_hp(1,stats.strength,run.loadout[id])+4
        assert m.hp==m.max_hp and m.first_miss_reroll_ready
    frozen=ExplorationRunState.model_validate_json(run.model_dump_json())
    assert frozen.talent_check_bonus==1
    choice=next(c for expedition in service.content.exploration_expeditions for event in expedition.events for c in event.choices if c.check).model_copy(deep=True)
    choice.check.mode='sum'
    preview=service._exploration_check_preview(run,choice,{'check_bonus':run.talent_check_bonus})
    base=sum(service.partner_catalog_loader().partner_map[id].exploration_stats.modifier(choice.check.attribute) for id in run.partner_ids)
    assert preview['modifier']==base+1
    assert all(m.first_miss_reroll_ready for m in frozen.combat_party.values())


def test_first_miss_reroll_is_per_member_per_run_and_uses_new_result():
    hero=member('hero');hero.state.first_miss_reroll_ready=True
    first=battle(enemy_hp=100)
    delve_battle.member_attack(FixedRandom([1,20,4,4]),first,hero,'beast#1')
    assert first.logs[-1].hit and first.logs[-1].critical
    assert first.logs[-1].rolls==[1,20]
    assert '临危应变' in first.logs[-1].text
    hero.state=DelveMemberState.model_validate_json(hero.state.model_dump_json())
    second=battle(enemy_hp=100)
    delve_battle.member_attack(FixedRandom([1,20]),second,hero,'beast#1')
    assert not second.logs[-1].hit and second.logs[-1].rolls==[1]
    other=member('other');other.state.first_miss_reroll_ready=True
    delve_battle.member_attack(FixedRandom([2,1]),second,other,'beast#1')
    assert not second.logs[-1].hit and second.logs[-1].roll==1
    assert not other.state.first_miss_reroll_ready


class SeaDice(random.Random):
    def __init__(self,dice):super().__init__(1);self.dice=iter(dice)
    def randint(self,a,b):return next(self.dice,1) if (a,b)==(1,20) else a
    def sample(self,population,k):
        return sorted(population,key=lambda e:e.id!='squall')[:k]


@pytest.mark.parametrize('rescued', [True,False])
def test_vanessa_sailing_bonus_first_failure_rescue_and_frozen_logs(sailing_game,rescued):
    service,repo,player,clock=sailing_game
    vanessa=load_partner_catalog().partner_map['vanessa']
    assert not vanessa.standard_recruitable and vanessa.recruitable and vanessa.artworks
    assert vanessa.rarity==5
    assert [(t.level_1,t.level_60) for t in vanessa.tendencies]==[(34,223),(27,168)]
    catalog=PartnerCatalog(partners=[*service.partner_catalog_loader().partners,vanessa])
    service.partner_catalog_loader=lambda:catalog
    service.admin_grant_partner(player.player_id,'vanessa')
    def prepare(p):p.sailing.completed_voyages=1;p.talent_nodes=['aquatic_ability_1']
    repo.update(player.player_id,prepare)
    service.rng=SeaDice([1,1,20 if rescued else 1,1,1])
    start_sailing(service,route='white_sail',party=['vanessa','sailor'])
    run=repo.get(player.player_id).sailing.active_run
    # 34*1.2 rounds to 41, sailor support 40*.25, global aquatic +10.
    assert run.ability==61
    logs=run.logs
    assert len(logs)==3
    assert logs[0].rescue_partner_id=='vanessa'
    assert logs[0].initial_roll==1 and logs[0].rerolls==[1,20 if rescued else 1]
    assert logs[0].success==rescued and logs[0].rescue_bonus_draws==int(rescued)
    assert not any(log.rescue_partner_id for log in logs[1:])
    for log in logs:
        actor=catalog.partner_map[log.actor_id]
        assert log.modifier==actor.exploration_stats.modifier(log.attribute)+2
    assert sum(drop.quantity for drop in run.drops)==17+(2 if rescued else 0)
    assert run.applied_effects[0]['trait_code']=='star_guidance'
    frozen=SailingRun.model_validate_json(run.model_dump_json())
    clock[0]=run.ready_at
    service.collect_sailing('sailing-sub',run.run_id)
    assert repo.get(player.player_id).sailing.last_run.logs==frozen.logs
    once=repo.get(player.player_id).inventory
    service.collect_sailing('sailing-sub',run.run_id)
    assert repo.get(player.player_id).inventory==once


def test_first_miss_reroll_after_accessory_advantage_logs_all_three_dice():
    hero=member('hero');hero.state.first_miss_reroll_ready=True
    fight=battle(enemy_hp=100)
    fight.advantage_ready['hero']=True
    delve_battle.member_attack(FixedRandom([1,2,20,4,4]),fight,hero,'beast#1')
    assert fight.logs[-1].rolls==[1,2,20]
    assert fight.logs[-1].hit
    assert not fight.advantage_ready['hero']
    assert not hero.state.first_miss_reroll_ready
