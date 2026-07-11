#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""複勝◎ を最強レバー(◎単勝オッズ短縮)起点にAND絞り込み。100%超えを探す。
毎段 n / 素ROI / 2024 / 2025 を出す。nが小さいセルは蜃気楼として明示。
最後に別母集団(2番人気=複勝○)で「オッズ短縮で複勝ROIが上がる」が同方向か追試。"""
import csv
from collections import defaultdict

BASE = "/Users/yamadori/keiba_data_src/common/data/df_csv/"

info = {}
with open(BASE + "df_race_info.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f, delimiter="\t"):
        info[r["race_id"]] = r

# 各馬の属性(人気別に使えるように全馬保持)
horses = defaultdict(dict)   # rid -> {pop -> dict}
field_n = defaultdict(int)
with open(BASE + "df_race.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        rid = row["race_id"]
        try:
            pop = int(float(row["popularity"])); uma = int(float(row["umaban"])); rank = int(float(row["rank"]))
            odds = float(row["tansho_odds"]); age = int(float(row["age"]))
        except (ValueError, TypeError):
            continue
        field_n[rid] += 1
        horses[rid][pop] = dict(uma=uma, rank=rank, odds=odds, age=age)

fuku = defaultdict(dict)
with open(BASE + "df_race_return.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        if row["bet_type"] != "複勝":
            continue
        try:
            fuku[row["race_id"]][int(row["win_umaban"])] = int(float(row["return"]))
        except (ValueError, TypeError):
            continue

rids = [r for r in horses if r in fuku and r in info and 1 in horses[r]]

def pay(rid, pop):
    h = horses[rid].get(pop)
    return fuku[rid].get(h["uma"], 0) if h else 0

def agg(subset, pop=1):
    sy = defaultdict(int); ry = defaultdict(int); ts = tr = 0
    for rid in subset:
        p = pay(rid, pop); y = info[rid]["date"][:4]
        sy[y]+=100; ry[y]+=p; ts+=100; tr+=p
    roi = tr/ts*100 if ts else 0
    r24 = ry["2024"]/sy["2024"]*100 if sy["2024"] else 0
    r25 = ry["2025"]/sy["2025"]*100 if sy["2025"] else 0
    return len(subset), roi, r24, r25

def show(label, sub, pop=1):
    n, roi, r24, r25 = agg(sub, pop)
    flag = ""
    if roi >= 100: flag = " ★100超"
    if n < 60: flag += " ⚠️n小(蜃気楼注意)"
    print(f"{label:<34}n={n:>4}  ROI={roi:>6.1f}%  24={r24:>5.0f}% 25={r25:>5.0f}%{flag}")

def O(r): return horses[r][1]["odds"]
def AGE(r): return horses[r][1]["age"]
def CLASS(r): return info[r]["race_class"]
def CLEN(r):
    try: return int(float(info[r]["course_len"]))
    except: return 0
def PLACE(r): return info[r]["place"]

print(f"■ 起点: 複勝◎ 全{len(rids)}R")
show("複勝◎ 全体", rids)
print()

print("■ レバー: ◎単勝オッズ短縮(favorite-longshot)")
for th in (2.0, 1.8, 1.6, 1.5, 1.4, 1.3, 1.2):
    show(f"◎odds<{th}", [r for r in rids if O(r)<th])
print()

print("■ AND1: ◎odds<1.5 に条件を1つ足す")
base = [r for r in rids if O(r)<1.5]
show("◎odds<1.5 (基準)", base)
show("  +3歳", [r for r in base if AGE(r)==3])
show("  +2-3歳", [r for r in base if AGE(r) in (2,3)])
show("  +place=7", [r for r in base if PLACE(r)=="7"])
show("  +〜1300m", [r for r in base if 0<CLEN(r)<=1300])
show("  +〜1600m", [r for r in base if 0<CLEN(r)<=1600])
show("  +class<=1(新馬未勝利)", [r for r in base if CLASS(r) in ("0","1")])
print()

print("■ AND2: ◎odds<1.6 で緩めてn確保しつつ重ねる")
base2 = [r for r in rids if O(r)<1.6]
show("◎odds<1.6 (基準)", base2)
show("  +2-3歳", [r for r in base2 if AGE(r) in (2,3)])
show("  +〜1600m", [r for r in base2 if 0<CLEN(r)<=1600])
show("  +2-3歳 & 〜1600m", [r for r in base2 if AGE(r) in (2,3) and 0<CLEN(r)<=1600])
show("  +class<=1", [r for r in base2 if CLASS(r) in ("0","1")])
show("  +class<=1 & 〜1600m", [r for r in base2 if CLASS(r) in ("0","1") and 0<CLEN(r)<=1600])
print()

print("■ AND3: 強い組み合わせを深掘り(n激減に注意)")
show("◎odds<1.4 & 2-3歳", [r for r in rids if O(r)<1.4 and AGE(r) in (2,3)])
show("◎odds<1.4 & class<=1", [r for r in rids if O(r)<1.4 and CLASS(r) in ("0","1")])
show("◎odds<1.5 & 2-3歳 & 〜1600m", [r for r in rids if O(r)<1.5 and AGE(r) in (2,3) and 0<CLEN(r)<=1600])
show("◎odds<1.3", [r for r in rids if O(r)<1.3])
print()

print("■ 追試: 別母集団=2番人気(複勝○)でも『オッズ短縮→複勝ROI上昇』が同方向か")
rids2 = [r for r in rids if 2 in horses[r]]
def O2(r): return horses[r][2]["odds"]
for th in (2.0, 2.5, 3.0, 4.0, 6.0, 99):
    lo = {2.0:0,2.5:2.0,3.0:2.5,4.0:3.0,6.0:4.0,99:6.0}[th]
    sub = [r for r in rids2 if lo<=O2(r)<th]
    show(f"○(2番人気) odds {lo}-{th}倍", sub, pop=2)
