"""设施改良策划的离线测算，不读取玩家存档，不修改游戏配置。

../../venv/bin/python scripts/facility_balance.py
../../venv/bin/python scripts/facility_balance.py --html docs/facility-balance.html
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
from html import escape
import json
from math import ceil, floor
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from red_leaf_town.domain.quality import quality_probabilities
from red_leaf_town.domain.aquatic import PondParameters, advance_pond, pond_cycle_seconds
from red_leaf_town.domain.models import PondState

GAME = json.loads((ROOT / "data/game.json").read_text())
ITEMS = {i["id"]: i for i in GAME["items"]}
RECIPES = {r["produce_item_id"]: r for r in GAME["recipes"]}
MULTIPLIERS = [1, 1.2, 1.5, 2, 3]
COEFFICIENTS = [1, .8, .6, .4]
QUALITY_NAMES = ["普通", "良品", "上品", "臻品"]
NEW_PRICES = {"blue_iron_ore": 40, "iron_ingot": 180, "wood_oil": 85,
              "precision_parts": 460, "sunflower_seed_produce": 65, "grape": 100,
              "pond_blue_carp": 40}
NEW_RECIPES = {
    "iron_ingot": (1, 3, 240, {"blue_iron_ore": 3}),
    "wood_oil": (2, 2, 180, {"sunflower_seed_produce": 2, "maple_resin": 1}),
    "precision_parts": (1, 4, 360, {"iron_ingot": 2, "red_copper_ore": 2, "moon_silver_ore": 1}),
}
BUILDINGS = [
    ("第一组改良田", 18, 6000, {"iron_ingot": 6, "wood_oil": 4, "rope": 4}),
    ("第二组改良田", 24, 10000, {"iron_ingot": 10, "wood_oil": 6, "rope": 6}),
    ("第三组改良田", 28, 16000, {"iron_ingot": 14, "wood_oil": 8, "rope": 8}),
    ("饲料槽Ⅰ", 18, 5000, {"iron_ingot": 6, "maple_plank": 12, "wood_oil": 4}),
    ("饲料槽Ⅱ", 26, 12000, {"precision_parts": 4, "composite_plank": 8, "wood_oil": 8}),
    ("青鲤塘", 22, 12000, {"iron_ingot": 8, "composite_plank": 12, "wood_oil": 6}),
    ("精制房", 20, 20000, {"iron_ingot": 12, "precision_parts": 4, "wood_oil": 8, "waterproof_canvas": 2}),
]
REFINING = [
    {"name": "南瓜果脯", "input_price": 50, "quantity": 3, "price": 240, "top_hours": 27},
    {"name": "青鲤鱼干", "input_price": 40, "quantity": 4, "price": 260, "top_hours": 40.5},
    {"name": "葡萄酒", "input_price": 100, "quantity": 3, "price": 480, "top_hours": 54},
]


def price(item_id):
    return NEW_PRICES[item_id] if item_id in NEW_PRICES else ITEMS[item_id]["sell_price"]


def refining_hours(recipe, ability, raw_quality, output_quality):
    cost = recipe["input_price"] * recipe["quantity"]
    ordinary_input_profit = round(recipe["price"] * MULTIPLIERS[output_quality - 1]) - cost
    baseline = Fraction(str(recipe["top_hours"])) * ordinary_input_profit / (recipe["price"] * 2 - cost)
    # 每个累计节点独立向上取整到秒，不把每档舍入误差累计下去。
    efficiency = 1 + Fraction(ability, ability + 100)
    seconds = ceil(baseline * 3600 / efficiency * Fraction(str(COEFFICIENTS[raw_quality - 1])))
    return seconds / 3600


def refining_profit(recipe, raw_quality, output_quality):
    return (floor(recipe["price"] * MULTIPLIERS[output_quality - 1] + .5)
            - floor(recipe["input_price"] * MULTIPLIERS[raw_quality - 1] + .5) * recipe["quantity"])


def expand_materials(required):
    """合并跨建设项目的需求，余料复用；按足量普通原料、无返料特性展开。"""
    recipes = {k: (r["produce_quantity"], r["stamina_cost"], r["duration_seconds"],
                   {i["item_id"]: i["quantity"] for i in r["inputs"]}) for k, r in RECIPES.items()}
    recipes.update(NEW_RECIPES)
    need, stock, raw = Counter(required), Counter(), Counter()
    stamina = seconds = 0
    batches = Counter()
    while need:
        item, count = need.popitem()
        used = min(stock[item], count)
        count -= used
        stock[item] -= used
        if not count:
            continue
        if item not in recipes:
            raw[item] += count
            continue
        output, energy, duration, inputs = recipes[item]
        runs = ceil(count / output)
        stock[item] += runs * output - count
        batches[item] += runs
        stamina += runs * energy
        seconds += runs * duration
        need.update({key: value * runs for key, value in inputs.items()})
    return {"raw": dict(sorted(raw.items())), "batches": dict(sorted(batches.items())),
            "stamina": stamina, "base_workbench_hours": seconds / 3600,
            "leftovers": {k: v for k, v in stock.items() if v}}


def crop_profit(price_, seed, hours, yields, thresholds, width, ability):
    probabilities = quality_probabilities(ability, thresholds, width, .003, True)
    revenue = yields * sum(p * floor(price_ * m + .5) for p, m in zip(probabilities, MULTIPLIERS))
    duration = hours / (1 + 2 * ability / (ability + 120))
    return (revenue - seed) / duration


def pond_comparison():
    rows = []
    for name, capacity, base, sale, feed, thresholds in [
        ("红鲫", 40, 7200, 22, 2, [40, 80, 150, 250]),
        ("青鲤", 60, 14400, 40, 3, [75, 125, 200, 310]),
    ]:
        for interval in (8, 12, 24):
            params = PondParameters(pond_cycle_seconds(base, 100, 150), capacity,
                                    .08, 3, .6, .6, .6, 60, feed)
            pond = PondState(pond_id="simulation", species_id="simulation", stock=params.steady_stock)
            income = cost = quantity = 0
            for step in range(28 * 24 // interval):
                cycles = (pond.settle_remainder + interval * 3600) // params.cycle_seconds
                advance_pond(pond, params, interval * 3600)
                caught = max(0, pond.stock - params.steady_stock)
                probabilities = quality_probabilities(150 + pond.generation_score, thresholds, 20, .003, True)
                if step >= 14 * 24 // interval:
                    quantity += caught
                    income += caught * sum(p * floor(sale*m + .5) for p, m in zip(probabilities, MULTIPLIERS))
                    cost += cycles * feed * 3
                pond.stock -= caught
            rows.append({"name": name, "interval": interval,
                         "fish_day": quantity / 14, "net_day": (income - cost) / 14})
    return rows


def mining_budget(raw, ability=100):
    """精确计算随机产量补齐指定库存的期望开工次数，含最后一轮余矿。"""
    definitions = [
        ("blue_iron_ore", 5, 7, 250, 4, 2880),
        ("red_copper_ore", 3, 4, 150, 1, 720),
        ("moon_silver_ore", 4, 5, 200, 2, 1440),
    ]
    rows = []
    for item, low, high, difficulty, stamina, seconds in definitions:
        low, high = floor(low * (1+ability/(ability+difficulty))), floor(high * (1+ability/(ability+difficulty)))
        expected = [0.0]
        for count in range(1, raw.get(item, 0)+1):
            expected.append(1 + sum(expected[max(0, count-y)] for y in range(low, high+1))/(high-low+1))
        rows.append({"item": item, "runs": expected[-1], "stamina": expected[-1]*stamina,
                     "site_hours": expected[-1]*seconds/3600})
    return rows


def results():
    materials = Counter()
    for _, _, _, ingredients in BUILDINGS:
        materials.update(ingredients)
    capital = sum(price(k) * v for k, v in materials.items())
    refine = []
    for recipe in REFINING:
        for raw in range(1, 5):
            for output in range(1, 5):
                hours = refining_hours(recipe, 200, raw, output)
                profit = refining_profit(recipe, raw, output)
                refine.append({"recipe": recipe["name"], "raw": raw, "output": output,
                               "hours": hours, "profit": profit, "room_day": profit / hours * 144})
    # 每8/12/24小时上线一次，并只在达到目标品质后收取；能力200可达到臻品。
    visits = []
    wine = REFINING[-1]
    for interval in (8, 12, 24):
        for raw in range(1, 5):
            choices = []
            for output in range(1, 5):
                duration = refining_hours(wine, 200, raw, output)
                actual = ceil(duration / interval) * interval
                # 到访时如果已经达到更高品质，按真实当前品质收取。
                reached = max(q for q in range(1, 5) if refining_hours(wine, 200, raw, q) <= actual)
                gain = refining_profit(wine, raw, reached)
                choices.append((gain / actual * 144, reached, actual))
            best = max(choices)
            visits.append({"interval": interval, "raw": raw, "room_day": best[0],
                           "collected_quality": best[1], "cycle_hours": best[2]})
    crop_rows = []
    for name, p, seed, h, y, th, width in [
        ("南瓜", 50, 80, 12, 3, [45, 90, 200, 320], 30),
        ("亚麻", 40, 106, 12, 5, [80, 140, 265, 400], 32),
        ("向日葵", 65, 180, 16, 5, [85, 145, 255, 390], 30),
        ("葡萄", 100, 300, 20, 5, [95, 155, 270, 410], 32),
    ]:
        crop_rows.append({"name": name, "rates": [crop_profit(p, seed, h, y, th, width, a) for a in (0, 100, 200)]})
    # 不是全系统仿真：这里给出普通原料、静态供料、无特性、无收取空档下限。
    chain = expand_materials(materials)
    return {"cash": sum(b[2] for b in BUILDINGS), "materials_value": capital,
            "construction_chain": chain, "mining": mining_budget(chain["raw"]),
            "room_chain": expand_materials(BUILDINGS[-1][3]),
            "refining": refine, "visits": visits, "crops": crop_rows, "ponds": pond_comparison()}


def check(result):
    assert result["cash"] == 81000
    assert result["materials_value"] == 31770
    for recipe in REFINING:
        for ability in (0, 40, 100, 200, 400):
            rates = []
            for raw in range(1, 5):
                times = [refining_hours(recipe, ability, raw, q) for q in range(1, 5)]
                assert times == sorted(times) and len(set(times)) == 4
                rates.append(refining_profit(recipe, raw, 4) / times[-1])
                if raw == 1:
                    all_rates = [refining_profit(recipe, raw, q) / times[q-1] for q in range(1, 5)]
                    assert max(all_rates) / min(all_rates) < 1.0002
            assert rates == sorted(rates), (recipe, ability, rates)


def write_html(path, result):
    theme = (ROOT / "frontend/src/styles.css").read_text().split("}", 1)[0] + "}"
    payload = json.dumps({"recipes": REFINING, "results": result}, ensure_ascii=False)
    rows = "".join(f"<tr><td>{escape(name)}</td><td>{level}</td><td>{coins:,}</td><td>{sum(price(k)*v for k,v in inputs.items()):,}</td></tr>"
                   for name, level, coins, inputs in BUILDINGS)
    raw_rows = "".join(f"<tr><td>{escape(ITEMS.get(k, {}).get('name', k))}</td><td>{v}</td></tr>"
                       for k, v in result["construction_chain"]["raw"].items())
    html = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>红叶镇设施改良 策划测算</title><style>THEME
*{box-sizing:border-box}body{margin:0;padding:28px}main{max-width:1120px;margin:auto}h1{font-size:30px}h2{font-size:20px;color:var(--gold)}p,small{line-height:1.8;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card,section{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:20px}.value{font-size:28px;color:var(--leaf-bright)}.controls{display:flex;gap:20px;flex-wrap:wrap;align-items:center}select,input{background:var(--surface-2);color:var(--cream);padding:8px;border:1px solid var(--line);border-radius:8px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:11px;text-align:left;border-bottom:1px solid var(--line)}.scroll{overflow-x:auto}svg{width:100%;min-width:460px;height:auto}.legend{display:flex;gap:22px;flex-wrap:wrap}.muted{color:var(--muted)}.warn{color:var(--gold)}a{color:var(--leaf-bright)}@media(max-width:700px){body{padding:12px}.grid{grid-template-columns:1fr}h1{font-size:25px}section{padding:15px}}@media print{body{background:white;color:black}section,.card{break-inside:avoid}small,p{color:#555}}</style>
<main><small>红叶镇物语 · 2026-10-05 · 设施改良 · 离线经济测算</small><h1>设施改良与精制</h1><p>六格精制房、合并农田、青鲤塘与饲料槽改良。报告沿用现有前端主题色；它是离线测算文档，不是游戏界面。<a href="facility-improvements.md">完整策划</a></p>
<div class="grid"><div class="card"><small>建设现金支出</small><div class="value">81,000 币</div></div><div class="card"><small>普通材料出售机会成本</small><div class="value">31,770 币</div></div><div class="card"><small>完整建设加工体力</small><div class="value">ENERGY 点</div><small>另需采矿体力与原料等待</small></div></div>
<section><h2>精制收益曲线</h2><div class="controls"><label>配方 <select id="recipe"></select></label><label>速度能力 A <input id="ability" type="range" min="0" max="300" value="200"> <span id="aval"></span></label><label>匠心 <input id="talent" type="checkbox" checked></label></div><p id="cap"></p><div id="legend" class="legend"></div><div class="scroll"><svg id="chart" viewBox="0 0 900 300" role="img" aria-label="四种原料在各成品品质节点的每小时净收益"></svg></div><div class="scroll"><table><thead><tr><th>原料</th><th>普通品</th><th>良品</th><th>上品</th><th>臻品</th></tr></thead><tbody id="rates"></tbody></table></div><p>单位：币／格／小时，扣除原料直接出售价值。每格固定一批、共六格。虚线段为当前能力无法到达的品质；时间向上取整到秒。原料减时系数为 1／0.8／0.6／0.4，奇迹原料不可投入。</p></section>
<section><h2>上线频率改变实际收益</h2><p>能力200，葡萄酒。每隔固定时间上线，模拟各目标品质策略，按到访时真实品质领取，选出该原料品质的最高收益策略。假设六格同步、立即补货且原料无限；不是存档预测。</p><div class="scroll"><table><thead><tr><th>到访间隔</th><th>普通原料</th><th>良品原料</th><th>上品原料</th><th>臻品原料</th></tr></thead><tbody id="visits"></tbody></table></div><p class="warn">高品质原料的优势在固定上线间隔下可能被等待空档抵消。表内为币／房／天，不含原料生产利润，不能与原料直接出售收入重复相加。</p></section>
<section><h2>建设投资</h2><div class="scroll"><table><thead><tr><th>设施</th><th>等级</th><th>现金</th><th>普通材料价值</th></tr></thead><tbody>BUILDROWS</tbody></table></div><p>全部材料链基础工位时间：WORKHOURS 小时。加工能力、特性、品质原料机会成本及采矿工时未计入；实际材料需求按整数批次并复用余料。</p><details><summary>查看全链基础原料需求</summary><table><thead><tr><th>原料</th><th>数量</th></tr></thead><tbody>RAWROWS</tbody></table></details></section>
<section><h2>农田规模</h2><p>20、24、28级各免费新增一块普通田，原始生产规模依次为9、10、11。全部改良后显示3块双倍田＋5块普通田，共8张田块卡片、11份单田产能。新增土地不增加农作编制，旧玩家按已达等级补齐；单田收益和建设费用不变。</p></section>
<section><h2>界面复用约束</h2><p>农田扩展 FarmPlot；新塘复用 PondCard；饲料槽扩展 SlotPanel。精制使用现有 ViewHeader、PartnerPicker、ModalSheet、ActionButton、QualityTag、ProgressBar、CostChip。ProductionCard 目前只表达单一完成时刻，不能直接把普通品可领取误当成任务结束；必要时添加薄的精制展示组件，继续使用全局 surface-card 和品质色变量。</p></section></main>
<script>const data=PAYLOAD;const names=['普通','良品','上品','臻品'],mult=[1,1.2,1.5,2],coef=[1,.8,.6,.4],colors=['#9baa9d','#79b388','#70afd6','#b58add'];const $=id=>document.getElementById(id);
data.recipes.forEach((r,i)=>$('recipe').add(new Option(r.name,i)));$('recipe').value='2';$('legend').innerHTML=names.map((n,i)=>`<span style="color:${colors[i]}">● ${n}原料</span>`).join('');
function render(){let r=data.recipes[+$('recipe').value],a=+$('ability').value,q=a+($('talent').checked?15:0),cap=q>=180?4:q>=120?3:q>=60?2:1; $('aval').textContent=a;$('cap').textContent=`品质能力 Q＝${q}，本批上限：${names[cap-1]}。能力不够的节点仅作为数值对照。`;
let costs=r.input_price*r.quantity, rows=coef.map((f,i)=>mult.map((m,j)=>{let t=Math.ceil(r.top_hours*(r.price*m-costs)/(r.price*2-costs)/(1+a/(a+100))*f*3600)/3600;return {t,rate:(Math.round(r.price*m)-Math.round(r.input_price*mult[i])*r.quantity)/t}}));
$('rates').innerHTML=rows.map((row,i)=>`<tr><td style="color:${colors[i]}">${names[i]}</td>${row.map((v,j)=>`<td style="opacity:${j<cap?1:.35}">${v.rate.toFixed(2)}<br><small>${v.t.toFixed(2)} 小时${j<cap?'':' · 不可达'}</small></td>`).join('')}</tr>`).join('');
let vals=rows.flat().map(v=>v.rate),lo=Math.min(0,...vals),hi=Math.max(1,...vals)*1.1;const X=j=>100+j*240,Y=v=>240-(v-lo)/(hi-lo)*200;let s='';for(let k=0;k<=4;k++){let v=lo+(hi-lo)*k/4,y=Y(v);s+=`<path d="M70 ${y}H855" stroke="#3a483c"/><text x="8" y="${y+4}" fill="#a9b2a8" font-size="13">${v.toFixed(1)}</text>`;}names.forEach((n,j)=>s+=`<text x="${X(j)}" y="282" fill="#f4eedf" text-anchor="middle">${n}成品</text>`);rows.forEach((row,i)=>{for(let j=1;j<4;j++)s+=`<path d="M${X(j-1)} ${Y(row[j-1].rate)} L${X(j)} ${Y(row[j].rate)}" stroke="${colors[i]}" stroke-width="3" ${j>=cap?'stroke-dasharray="5 5" opacity=".35"':''}/>`;row.forEach((v,j)=>s+=`<circle cx="${X(j)}" cy="${Y(v.rate)}" r="4" fill="${colors[i]}" opacity="${j<cap?1:.35}"/>`)});$('chart').innerHTML=s;}
['recipe','ability','talent'].forEach(id=>$(id).addEventListener('input',render));render();$('visits').innerHTML=[8,12,24].map(h=>`<tr><td>${h}小时</td>${data.results.visits.filter(v=>v.interval===h).map(v=>`<td>${v.room_day.toFixed(0)}<br><small>${v.cycle_hours}h收${names[v.collected_quality-1]}</small></td>`).join('')}</tr>`).join('');</script></html>'''
    html = html.replace("THEME", theme).replace("PAYLOAD", payload).replace("BUILDROWS", rows).replace("RAWROWS", raw_rows)
    html = html.replace("ENERGY", str(result["construction_chain"]["stamina"]))
    html = html.replace("WORKHOURS", f'{result["construction_chain"]["base_workbench_hours"]:.2f}')
    path.write_text(html)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path)
    args = parser.parse_args()
    report = results()
    check(report)
    if args.html:
        write_html(args.html, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
