#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""オッズを固定し、残り4軸(血統/年齢/性別/クラス)スコアが独立に効くか検証。
有利方向は学習窓で定義。検証窓で [オッズ帯 × 4軸スコア] 別ROI。
オッズ帯内で4軸スコアが効かなければ=4軸はオッズの言い換えでしかない(独立エッジ無し)。"""
import sqlite3
from collections import defaultdict

DB = "/Users/yamadori/keiba-auto-research/keiba-dashboard/keiba.db"
con = sqlite3.connect(DB)
rows = con.execute("""
SELECT year, win_odds, sire, age, gender, class, place_payout
FROM entries WHERE popularity=1 AND win_odds IS NOT NULL AND finish IS NOT NULL AND year>=2014
  AND sire IS NOT NULL AND age IS NOT NULL AND gender IS NOT NULL AND class IS NOT NULL
""").fetchall()
con.close()

def age_b(a): return "2" if a<=2 else ("3" if a==3 else ("4" if a==4 else "5+"))
def cls_b(c):
    if c in ("新馬","未勝利"): return "新馬未勝利"
    if c in ("500万","1勝"): return "1勝"
    if c in ("1000万","2勝"): return "2勝"
    if c in ("1600万","3勝"): return "3勝"
    return "OP+"
def odds_b(o):
    if o<1.8: return "◎堅 <1.8"
    if o<2.5: return "◎中 1.8-2.5"
    if o<3.5: return "◎並 2.5-3.5"
    return "◎緩 3.5+"
def pay(p): return p if p is not None else 0

learn=[r for r in rows if 2014<=r[0]<=2021]; valid=[r for r in rows if r[0]>=2022]
base=sum(pay(r[6]) for r in learn)/len(learn)

# オッズ以外4軸の有利flagを学習窓で定義
AX={"sire":(lambda r:r[2],50),"age":(lambda r:age_b(r[3]),1),"gender":(lambda r:r[4],1),"class":(lambda r:cls_b(r[5]),1)}
fav={}
for ax,(fn,mn) in AX.items():
    agg=defaultdict(lambda:[0,0])
    for r in learn:
        agg[fn(r)][0]+=1; agg[fn(r)][1]+=pay(r[6])
    fav[ax]={k for k,(n,s) in agg.items() if n>=mn and s/n>=base}

def score4(r): return sum(1 for ax,(fn,mn) in AX.items() if fn(r) in fav[ax])

# 検証窓: オッズ帯 × 4軸スコア群(低0-1/中2/高3-4)
def grp(s): return "低(0-1)" if s<=1 else ("中(2)" if s==2 else "高(3-4)")
cell=defaultdict(lambda:[0,0])
for r in valid:
    cell[(odds_b(r[1]), grp(score4(r)))][0]+=1
    cell[(odds_b(r[1]), grp(score4(r)))][1]+=pay(r[6])

print(f"学習窓基準ROI={base:.1f}%  検証窓で [オッズ帯 × オッズ以外4軸スコア] 別 複勝◎ROI\n")
print(f"{'オッズ帯':<14}{'4軸低(0-1)':>14}{'4軸中(2)':>14}{'4軸高(3-4)':>14}")
for ob in ["◎堅 <1.8","◎中 1.8-2.5","◎並 2.5-3.5","◎緩 3.5+"]:
    line=f"{ob:<14}"
    for g in ["低(0-1)","中(2)","高(3-4)"]:
        n,s=cell[(ob,g)]
        line += f"{(str(round(s/n))+'% n'+str(n)) if n else '-':>16}" if n else f"{'-':>16}"
    print(line)

print("\n=== 各オッズ帯内で 4軸高-低 の差(独立エッジの有無) ===")
for ob in ["◎堅 <1.8","◎中 1.8-2.5","◎並 2.5-3.5","◎緩 3.5+"]:
    lo=cell[(ob,"低(0-1)")]; hi=cell[(ob,"高(3-4)")]
    if lo[0] and hi[0]:
        d=hi[1]/hi[0]-lo[1]/lo[0]
        print(f"{ob:<14} 高{hi[1]/hi[0]:.0f}% - 低{lo[1]/lo[0]:.0f}% = {d:+.1f}pt  (n高{hi[0]}/低{lo[0]})")
