#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ハイブリッド馬券術の検証。
主張: 1券種の多点買いをやめ、複数券種に分散すると「回収率が爆上がり」する。
検証: 予想の質を排除するため◎○▲を人気1・2・3番人気で全レース機械固定し、
      買い方(券種の組み合わせ)だけを変えてROI・的中率を比較する。
データ: df_race.csv(人気/着順), df_race_return.csv(全券種配当, 2024-25, 100円あたり)
"""
import csv
from collections import defaultdict

BASE = "/Users/yamadori/keiba_data_src/common/data/df_csv/"

# --- df_race.csv: race_id -> {umaban: (popularity, rank)} ---
race_horses = defaultdict(dict)   # race_id -> {umaban:int -> (pop:int, rank:int)}
with open(BASE + "df_race.csv", encoding="utf-8") as f:
    r = csv.DictReader(f, delimiter="\t")
    for row in r:
        rid = row["race_id"]
        try:
            umaban = int(float(row["umaban"]))
            pop = int(float(row["popularity"]))
            rank = int(float(row["rank"]))
        except (ValueError, TypeError):
            continue
        race_horses[rid][umaban] = (pop, rank)

# --- df_race_return.csv: race_id -> {bet_type -> {combo_str -> payout}} (dedup) ---
payouts = defaultdict(lambda: defaultdict(dict))
with open(BASE + "df_race_return.csv", encoding="utf-8") as f:
    r = csv.DictReader(f, delimiter="\t")
    for row in r:
        rid = row["race_id"]
        bt = row["bet_type"]
        combo = row["win_umaban"]
        try:
            ret = int(float(row["return"]))
        except (ValueError, TypeError):
            continue
        payouts[rid][bt][combo] = ret   # dedup: 同一キーは上書き

# --- 各レースの◎○▲(人気1,2,3位)と着順1,2,3の馬番を確定 ---
def marks_and_result(rid):
    horses = race_horses.get(rid)
    if not horses or len(horses) < 5:
        return None
    pop2uma = {}
    rank2uma = {}
    for uma, (pop, rank) in horses.items():
        if pop in pop2uma:   # 人気重複(欠落) → 除外
            return None
        pop2uma[pop] = uma
        if 1 <= rank <= 3:
            rank2uma[rank] = uma
    if not all(p in pop2uma for p in (1, 2, 3)):
        return None
    if not all(k in rank2uma for k in (1, 2, 3)):
        return None
    return {
        "hon": pop2uma[1], "tai": pop2uma[2], "ana": pop2uma[3],
        "r1": rank2uma[1], "r2": rank2uma[2], "r3": rank2uma[3],
    }

# 券種の配当参照ヘルパ (combo表記は netkeiba: 馬連/3連複はソート済み"a-b", 馬単/3連単は着順"a-b")
def pay_of(rid, bt, combo):
    return payouts.get(rid, {}).get(bt, {}).get(combo, 0)

def umaren(rid, a, b):      # 馬連 {a,b}
    x, y = sorted((a, b))
    return pay_of(rid, "馬連", f"{x}-{y}")

def wide(rid, a, b):
    x, y = sorted((a, b))
    return pay_of(rid, "ワイド", f"{x}-{y}")

def umatan(rid, a, b):      # 馬単 a→b
    return pay_of(rid, "馬単", f"{a}-{b}")

def sanpuku(rid, a, b, c):  # 3連複 {a,b,c}
    x, y, z = sorted((a, b, c))
    return pay_of(rid, "三連複", f"{x}-{y}-{z}")

def santan(rid, a, b, c):   # 3連単 a→b→c
    return pay_of(rid, "三連単", f"{a}-{b}-{c}")

def tan(rid, a):
    return pay_of(rid, "単勝", str(a))

def fuku(rid, a):
    return pay_of(rid, "複勝", str(a))

# --- 戦略定義: 各戦略は (bets_placed点数, payout合計) を返す。100円/点。---
# 予想印: 本命=◎(1番人気), 対抗=○(2番人気), 穴=▲(3番人気)
def strat_tan_hon(m, rid):        # 単勝◎ 1点
    return 1, tan(rid, m["hon"])

def strat_fuku_hon(m, rid):       # 複勝◎ 1点
    return 1, fuku(rid, m["hon"])

def strat_umaren_hon_tai(m, rid): # 馬連◎-○ 1点
    return 1, umaren(rid, m["hon"], m["tai"])

def strat_umatan_hon_tai(m, rid): # 馬単◎→○ 1点
    return 1, umatan(rid, m["hon"], m["tai"])

def strat_sanpuku_3(m, rid):      # 3連複◎○▲ 1点
    return 1, sanpuku(rid, m["hon"], m["tai"], m["ana"])

def strat_santan_3(m, rid):       # 3連単◎→○→▲ 1点
    return 1, santan(rid, m["hon"], m["tai"], m["ana"])

# NGな買い方: 1券種の多点買い(人気上位5頭ボックス)
def strat_umaren_box5(m, rid):    # 馬連 上位5人気BOX 10点
    horses = race_horses[rid]
    pop2uma = {p: u for u, (p, _) in horses.items()}
    top5 = [pop2uma[p] for p in range(1, 6) if p in pop2uma]
    if len(top5) < 5:
        return None
    total = 0
    n = 0
    for i in range(5):
        for j in range(i + 1, 5):
            total += umaren(rid, top5[i], top5[j]); n += 1
    return n, total

def strat_sanpuku_box5(m, rid):   # 3連複 上位5人気BOX 10点
    horses = race_horses[rid]
    pop2uma = {p: u for u, (p, _) in horses.items()}
    top5 = [pop2uma[p] for p in range(1, 6) if p in pop2uma]
    if len(top5) < 5:
        return None
    total = 0; n = 0
    for i in range(5):
        for j in range(i + 1, 5):
            for k in range(j + 1, 5):
                total += sanpuku(rid, top5[i], top5[j], top5[k]); n += 1
    return n, total

def strat_santan_box4(m, rid):    # 3連単 上位4人気BOX 24点
    horses = race_horses[rid]
    pop2uma = {p: u for u, (p, _) in horses.items()}
    top4 = [pop2uma[p] for p in range(1, 5) if p in pop2uma]
    if len(top4) < 4:
        return None
    total = 0; n = 0
    from itertools import permutations
    for a, b, c in permutations(top4, 3):
        total += santan(rid, a, b, c); n += 1
    return n, total

# ハイブリッド(動画の1000円例に近い形): 馬単◎↔○(2) + 馬連◎-▲(1) + 3連複◎○▲(1)
def strat_hybrid_video(m, rid):
    total = 0; n = 0
    total += umatan(rid, m["hon"], m["tai"]); n += 1
    total += umatan(rid, m["tai"], m["hon"]); n += 1   # 裏表
    total += umaren(rid, m["hon"], m["ana"]); n += 1
    total += sanpuku(rid, m["hon"], m["tai"], m["ana"]); n += 1
    return n, total

# ハイブリッド(全部乗せ): 単◎ + 馬連◎○ + 馬単◎→○ + 3連複◎○▲ + 3連単◎→○→▲
def strat_hybrid_all(m, rid):
    total = 0; n = 0
    total += tan(rid, m["hon"]); n += 1
    total += umaren(rid, m["hon"], m["tai"]); n += 1
    total += umatan(rid, m["hon"], m["tai"]); n += 1
    total += sanpuku(rid, m["hon"], m["tai"], m["ana"]); n += 1
    total += santan(rid, m["hon"], m["tai"], m["ana"]); n += 1
    return n, total

STRATS = [
    ("単勝◎",                strat_tan_hon),
    ("複勝◎",                strat_fuku_hon),
    ("馬連◎-○",             strat_umaren_hon_tai),
    ("馬単◎→○",            strat_umatan_hon_tai),
    ("3連複◎○▲",           strat_sanpuku_3),
    ("3連単◎→○→▲",       strat_santan_3),
    ("[NG]馬連5頭BOX(10点)", strat_umaren_box5),
    ("[NG]3連複5頭BOX(10点)",strat_sanpuku_box5),
    ("[NG]3連単4頭BOX(24点)",strat_santan_box4),
    ("★ハイブリッド動画型",  strat_hybrid_video),
    ("★ハイブリッド全部乗せ",strat_hybrid_all),
]

# --- 集計 ---
valid_rids = []
for rid in payouts:
    if marks_and_result(rid):
        valid_rids.append(rid)

print(f"対象レース数: {len(valid_rids)} (2024-25, 人気1-3位と着順1-3が確定できるレース)")
print()
print(f"{'戦略':<22}{'投票額':>10}{'払戻':>12}{'ROI':>9}{'的中率':>9}{'的中R':>8}")
print("-" * 72)

for name, fn in STRATS:
    stake = 0
    ret = 0
    hit_races = 0
    n_races = 0
    for rid in valid_rids:
        m = marks_and_result(rid)
        res = fn(m, rid)
        if res is None:
            continue
        n_bets, payout = res
        stake += n_bets * 100
        ret += payout
        n_races += 1
        if payout > 0:
            hit_races += 1
    roi = ret / stake * 100 if stake else 0
    hr = hit_races / n_races * 100 if n_races else 0
    print(f"{name:<22}{stake:>10,}{ret:>12,}{roi:>8.1f}%{hr:>8.1f}%{hit_races:>8}")
