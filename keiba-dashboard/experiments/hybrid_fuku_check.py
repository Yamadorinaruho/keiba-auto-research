#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""複勝◎ROI 85.6% の追試。
①複勝配当データの健全性 ②人気別複勝ROI(単調性=favorite-longshot bias本物か)
③年別 ④最高配当除外。
"""
import csv
from collections import defaultdict

BASE = "/Users/yamadori/keiba_data_src/common/data/df_csv/"

race_horses = defaultdict(dict)   # rid -> {umaban -> (pop, rank)}
race_year = {}
with open(BASE + "df_race.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        rid = row["race_id"]
        try:
            umaban = int(float(row["umaban"])); pop = int(float(row["popularity"])); rank = int(float(row["rank"]))
        except (ValueError, TypeError):
            continue
        race_horses[rid][umaban] = (pop, rank)
        race_year[rid] = rid[:4]

fuku = defaultdict(dict)  # rid -> {umaban -> payout}
with open(BASE + "df_race_return.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        if row["bet_type"] != "複勝":
            continue
        try:
            fuku[row["race_id"]][int(row["win_umaban"])] = int(float(row["return"]))
        except (ValueError, TypeError):
            continue

# ---- ①健全性: 複勝配当の分布・異常値 ----
all_pays = [p for d in fuku.values() for p in d.values()]
all_pays.sort()
print("=== ①複勝配当データの健全性 ===")
print(f"複勝配当レコード数: {len(all_pays):,}")
print(f"最小: {all_pays[0]}  中央値: {all_pays[len(all_pays)//2]}  最大: {all_pays[-1]}")
print(f"100円未満(元返し割れ)の数: {sum(1 for p in all_pays if p < 100)}  ※複勝は100円以上が正常")
print(f"1レースの複勝配当件数の分布(3頭立て以上は通常3件):")
cnts = defaultdict(int)
for d in fuku.values():
    cnts[len(d)] += 1
for k in sorted(cnts):
    print(f"  {k}件: {cnts[k]}レース")
print()

# ---- 対象レース: 人気と着順が引けるレース ----
def pop_maps(rid):
    horses = race_horses.get(rid)
    if not horses or len(horses) < 5:
        return None
    pop2uma = {}
    for uma, (pop, rank) in horses.items():
        if pop in pop2uma:
            return None
        pop2uma[pop] = uma
    return pop2uma

# ---- ②人気別複勝ROI ----
print("=== ②人気別 複勝ROI(単調に減れば本物のfavorite-longshot bias) ===")
print(f"{'人気':>4}{'投票額':>10}{'払戻':>11}{'ROI':>8}{'的中率':>8}")
by_pop_stake = defaultdict(int); by_pop_ret = defaultdict(int); by_pop_hit = defaultdict(int); by_pop_n = defaultdict(int)
for rid, fp in fuku.items():
    pm = pop_maps(rid)
    if not pm:
        continue
    for pop in range(1, 11):
        if pop not in pm:
            continue
        uma = pm[pop]
        pay = fp.get(uma, 0)
        by_pop_stake[pop] += 100; by_pop_ret[pop] += pay; by_pop_n[pop] += 1
        if pay > 0:
            by_pop_hit[pop] += 1
for pop in range(1, 11):
    s = by_pop_stake[pop]
    if not s:
        continue
    roi = by_pop_ret[pop]/s*100
    hr = by_pop_hit[pop]/by_pop_n[pop]*100
    print(f"{pop:>4}{s:>10,}{by_pop_ret[pop]:>11,}{roi:>7.1f}%{hr:>7.1f}%")
print()

# ---- ③年別 複勝◎ ----
print("=== ③年別 複勝◎(1番人気) ===")
yr_s = defaultdict(int); yr_r = defaultdict(int)
hon_pays = []  # ④用
for rid, fp in fuku.items():
    pm = pop_maps(rid)
    if not pm or 1 not in pm:
        continue
    pay = fp.get(pm[1], 0)
    y = race_year[rid]
    yr_s[y] += 100; yr_r[y] += pay
    hon_pays.append(pay)
for y in sorted(yr_s):
    print(f"  {y}: ROI {yr_r[y]/yr_s[y]*100:.1f}%  (n={yr_s[y]//100})")
print()

# ---- ④最高配当除外(複勝は配当小なのでほぼ動かないはず) ----
tot_s = len(hon_pays)*100
tot_r = sum(hon_pays)
hon_pays_sorted = sorted(hon_pays, reverse=True)
print("=== ④複勝◎ 最高配当除外 ===")
print(f"素:      ROI {tot_r/tot_s*100:.1f}%  (n={len(hon_pays)})")
for k in (1, 3, 10):
    r = sum(hon_pays_sorted[k:])
    print(f"上位{k}本除外: ROI {r/((len(hon_pays)-k)*100)*100:.1f}%")
print(f"複勝◎の最高配当: {hon_pays_sorted[:5]} 円(100円あたり)")
