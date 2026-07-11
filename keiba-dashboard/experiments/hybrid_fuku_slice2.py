#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""複勝◎ 追加軸: 月/季節/性別(牡牝セン)/性別×年齢/回り/天候/枠番。
複勝◎全体(n≈4839) と favorite-longshotレバー内(◎odds<2.0, n≈1193)の両方で見る。
各セル n / ROI / 2024 / 2025。100超えは★、n<60は蜃気楼⚠️。"""
import csv
from collections import defaultdict

BASE = "/Users/yamadori/keiba_data_src/common/data/df_csv/"
SEX = {"0": "牡", "1": "牝", "2": "セ"}

info = {}
with open(BASE + "df_race_info.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f, delimiter="\t"):
        info[r["race_id"]] = r

hon = {}
with open(BASE + "df_race.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        try:
            if int(float(row["popularity"])) != 1:
                continue
            rid = row["race_id"]
            hon[rid] = dict(
                uma=int(float(row["umaban"])), odds=float(row["tansho_odds"]),
                age=int(float(row["age"])), sex=row["sex"],
                waku=int(float(row["wakuban"])) if row["wakuban"] else 0,
            )
        except (ValueError, TypeError):
            continue

fuku = defaultdict(dict)
with open(BASE + "df_race_return.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        if row["bet_type"] != "複勝":
            continue
        try:
            fuku[row["race_id"]][int(row["win_umaban"])] = int(float(row["return"]))
        except (ValueError, TypeError):
            continue

rids = [r for r in hon if r in fuku and r in info]

def pay(rid): return fuku[rid].get(hon[rid]["uma"], 0)
def MONTH(r): return int(info[r]["date"][5:7])
def SEASON(r):
    m = MONTH(r)
    return {12:"冬",1:"冬",2:"冬",3:"春",4:"春",5:"春",6:"夏",7:"夏",8:"夏",9:"秋",10:"秋",11:"秋"}[m]

def agg(sub):
    sy=defaultdict(int); ry=defaultdict(int); ts=tr=0
    for rid in sub:
        p=pay(rid); y=info[rid]["date"][:4]
        sy[y]+=100; ry[y]+=p; ts+=100; tr+=p
    roi=tr/ts*100 if ts else 0
    return len(sub), roi, (ry["2024"]/sy["2024"]*100 if sy["2024"] else 0), (ry["2025"]/sy["2025"]*100 if sy["2025"] else 0)

def show(label, sub):
    n,roi,r24,r25=agg(sub)
    flag=""
    if roi>=100: flag=" ★100超"
    if 0<n<60: flag+=" ⚠️n小"
    print(f"{label:<24}n={n:>4}  ROI={roi:>6.1f}%  24={r24:>5.0f}% 25={r25:>5.0f}%{flag}")

def block(title, pool):
    print(f"\n######## {title} (母数{len(pool)}) ########")
    print("--- 月別 ---")
    for m in range(1,13):
        show(f"{m}月", [r for r in pool if MONTH(r)==m])
    print("--- 季節 ---")
    for s in ("春","夏","秋","冬"):
        show(s, [r for r in pool if SEASON(r)==s])
    print("--- 性別 ---")
    for sx in ("0","1","2"):
        show(f"{SEX[sx]}", [r for r in pool if hon[r]["sex"]==sx])
    print("--- 性別×年齢(主要) ---")
    for sx in ("0","1"):
        for a in (2,3,4,5):
            show(f"{SEX[sx]}{a}歳", [r for r in pool if hon[r]["sex"]==sx and hon[r]["age"]==a])
    print("--- 枠番 ---")
    for w in range(1,9):
        show(f"{w}枠", [r for r in pool if hon[r]["waku"]==w])

block("複勝◎ 全体", rids)
block("複勝◎ × ◎odds<2.0 (favorite-longshotレバー内)", [r for r in rids if hon[r]["odds"]<2.0])

# 有望AND探索: 夏 or 特定季節 × オッズ短縮 × 性別
print("\n######## AND探索(季節×オッズ×性別/年齢) ########")
for label, cond in [
    ("夏 & odds<2.0", lambda r: SEASON(r)=="夏" and hon[r]["odds"]<2.0),
    ("夏 & odds<1.6", lambda r: SEASON(r)=="夏" and hon[r]["odds"]<1.6),
    ("冬 & odds<2.0", lambda r: SEASON(r)=="冬" and hon[r]["odds"]<2.0),
    ("春 & odds<2.0", lambda r: SEASON(r)=="春" and hon[r]["odds"]<2.0),
    ("秋 & odds<2.0", lambda r: SEASON(r)=="秋" and hon[r]["odds"]<2.0),
    ("牝 & odds<2.0", lambda r: hon[r]["sex"]=="1" and hon[r]["odds"]<2.0),
    ("牡 & odds<2.0", lambda r: hon[r]["sex"]=="0" and hon[r]["odds"]<2.0),
    ("牝3歳 & odds<2.0", lambda r: hon[r]["sex"]=="1" and hon[r]["age"]==3 and hon[r]["odds"]<2.0),
    ("牡3歳 & odds<2.0", lambda r: hon[r]["sex"]=="0" and hon[r]["age"]==3 and hon[r]["odds"]<2.0),
    ("夏 & 牝 & odds<2.5", lambda r: SEASON(r)=="夏" and hon[r]["sex"]=="1" and hon[r]["odds"]<2.5),
    ("3歳 & odds<1.8", lambda r: hon[r]["age"]==3 and hon[r]["odds"]<1.8),
]:
    show(label, [r for r in rids if cond(r)])
