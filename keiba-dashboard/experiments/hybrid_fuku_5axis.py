#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""複勝◎ 5軸フルスキャン: オッズ×血統(父)×年齢×性別×クラス。
keiba.db 2010-2026・1番人気。両窓(学習14-21/検証22-26)それぞれで集計し、
厳格な生存ルール[両窓n≥20 かつ 両窓ROI≥100%]で蜃気楼を除外。
生存セルを出し、多重比較の偽陽性期待数も明示する。"""
import sqlite3
from collections import defaultdict

DB = "/Users/yamadori/keiba-auto-research/keiba-dashboard/keiba.db"
con = sqlite3.connect(DB)
cur = con.cursor()
rows = cur.execute("""
SELECT year, win_odds, sire, age, gender, class, finish, place_payout
FROM entries
WHERE popularity=1 AND win_odds IS NOT NULL AND finish IS NOT NULL AND year>=2014
""").fetchall()
con.close()

def odds_band(o):
    if o < 1.5: return "o<1.5"
    if o < 2.0: return "o1.5-2"
    if o < 3.0: return "o2-3"
    return "o3+"

def age_band(a):
    if a is None: return None
    if a <= 2: return "2歳"
    if a == 3: return "3歳"
    if a == 4: return "4歳"
    return "5歳+"

def class_grp(c):
    if c in ("新馬", "未勝利"): return "新馬未勝利"
    if c in ("500万", "1勝"): return "1勝"
    if c in ("1000万", "2勝"): return "2勝"
    if c in ("1600万", "3勝"): return "3勝"
    return "OP以上"

# cell -> window -> [n, ret]
cells = defaultdict(lambda: {"L": [0, 0], "V": [0, 0]})
for year, odds, sire, age, gender, cls, finish, ppay in rows:
    if sire is None or age is None or gender is None or cls is None:
        continue
    w = "L" if 2014 <= year <= 2021 else "V"
    key = (odds_band(odds), sire, age_band(age), gender, class_grp(cls))
    p = ppay if ppay is not None else 0
    cells[key]["L" if w == "L" else "V"][0] += 1
    cells[key][w][1] += p

# 生存判定
survivors = []
near = []
scanned = 0
for key, wd in cells.items():
    nL, rL = wd["L"]; nV, rV = wd["V"]
    if nL < 20 or nV < 20:
        continue
    scanned += 1
    roiL = rL / nL; roiV = rV / nV
    if roiL >= 100 and roiV >= 100:
        survivors.append((key, nL, roiL, nV, roiV))
    elif (roiL + roiV) / 2 >= 95:
        near.append((key, nL, roiL, nV, roiV))

print(f"両窓ともn≥20のセル数(スキャン対象): {scanned}")
print(f"※各窓ROI≥100を偶然満たす確率を各窓5割と粗く見ても両窓同時は約0.25→偽陽性期待 {scanned*0.06:.0f}〜{scanned*0.25:.0f}セル程度は出うる\n")

def fmt(rec):
    (ob, sire, ab, g, cg), nL, roiL, nV, roiV = rec
    return f"{ob:<7}{sire:<14}{ab:<5}{g:<3}{cg:<10} L:n{nL:>3}/{roiL:>5.0f}%  V:n{nV:>3}/{roiV:>5.0f}%"

print(f"=== 生存(両窓n≥20 & 両窓ROI≥100%): {len(survivors)}セル ===")
for rec in sorted(survivors, key=lambda x: -(x[2]+x[4])):
    print(fmt(rec))
if not survivors:
    print("  なし")

print(f"\n=== 惜しい(両窓平均≥95%, 両窓n≥20): {len(near)}セル ===")
for rec in sorted(near, key=lambda x: -(x[2]+x[4]))[:15]:
    print(fmt(rec))
