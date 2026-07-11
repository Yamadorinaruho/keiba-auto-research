#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""複勝◎ 5軸スコア方式(k個一致で買う)をリークなしで検証。
有利方向は学習窓(2014-2021)のバケット別ROIで定義(基準=学習窓全体ROIを上回るバケット=有利flag1)。
そのflagを検証窓(2022-2026)の各◎に適用し、スコア(0-5)別に検証窓ROIを見る。
スコアが上がるほどROIが上がるか&score高で100%超えるかを out-of-sample で判定。"""
import sqlite3
from collections import defaultdict

DB = "/Users/yamadori/keiba-auto-research/keiba-dashboard/keiba.db"
con = sqlite3.connect(DB)
rows = con.execute("""
SELECT year, win_odds, sire, age, gender, class, place_payout
FROM entries
WHERE popularity=1 AND win_odds IS NOT NULL AND finish IS NOT NULL AND year>=2014
  AND sire IS NOT NULL AND age IS NOT NULL AND gender IS NOT NULL AND class IS NOT NULL
""").fetchall()
con.close()

def odds_b(o):
    for hi,lab in [(1.5,"<1.5"),(1.8,"1.5-1.8"),(2.0,"1.8-2.0"),(2.5,"2-2.5"),(3.0,"2.5-3"),(4.0,"3-4")]:
        if o<hi: return lab
    return "4+"
def age_b(a): return "2" if a<=2 else ("3" if a==3 else ("4" if a==4 else "5+"))
def cls_b(c):
    if c in ("新馬","未勝利"): return "新馬未勝利"
    if c in ("500万","1勝"): return "1勝"
    if c in ("1000万","2勝"): return "2勝"
    if c in ("1600万","3勝"): return "3勝"
    return "OP+"

def pay(p): return p if p is not None else 0

# --- 学習窓でバケット別ROIを集計 → 有利flag定義 ---
learn = [r for r in rows if 2014 <= r[0] <= 2021]
valid = [r for r in rows if r[0] >= 2022]

base_learn = sum(pay(r[6]) for r in learn) / len(learn)
print(f"学習窓 複勝◎ 基準ROI = {base_learn:.1f}%  (これを上回るバケットを『有利』とする)\n")

def axis_roi(data, keyfn, min_n=1):
    agg = defaultdict(lambda:[0,0])
    for r in data:
        k = keyfn(r); agg[k][0]+=1; agg[k][1]+=pay(r[6])
    return {k:(v[0], v[1]/v[0]) for k,v in agg.items() if v[0]>=min_n}

AX = {
    "odds":   (lambda r: odds_b(r[1]), 1),
    "sire":   (lambda r: r[2], 50),     # 種牡馬は学習n≥50のみ判定、他は不利側(flag0)
    "age":    (lambda r: age_b(r[3]), 1),
    "gender": (lambda r: r[4], 1),
    "class":  (lambda r: cls_b(r[5]), 1),
}
fav = {}   # axis -> set(有利バケット)
for ax,(fn,mn) in AX.items():
    roi = axis_roi(learn, fn, mn)
    fav[ax] = {k for k,(n,r) in roi.items() if r >= base_learn}
    if ax in ("odds","age","gender","class"):
        print(f"[{ax}] 有利バケット(学習ROI≥基準): " + ", ".join(f"{k}({r:.0f})" for k,(n,r) in sorted(roi.items(), key=lambda x:-x[1][1])))
print(f"[sire] 有利種牡馬(学習n≥50 & ROI≥基準): {len(fav['sire'])}頭\n")

def score(r):
    s = 0
    for ax,(fn,mn) in AX.items():
        if fn(r) in fav[ax]:
            s += 1
    return s

# --- 検証窓でスコア別ROI(out-of-sample) ---
def by_score(data, tag):
    agg = defaultdict(lambda:[0,0])
    for r in data:
        s = score(r); agg[s][0]+=1; agg[s][1]+=pay(r[6])
    print(f"=== {tag} スコア別 複勝◎ROI ===")
    print(f"{'score':>5}{'n':>7}{'ROI':>8}{'累計(score≥k)':>16}")
    # 個別
    for s in range(6):
        if s in agg:
            n,ret=agg[s]; print(f"{s:>5}{n:>7}{ret/n:>7.0f}%")
    # 累計 score≥k
    print("  --- score≥k で買う ---")
    for k in range(6):
        ns=sum(agg[s][0] for s in agg if s>=k); rs=sum(agg[s][1] for s in agg if s>=k)
        if ns:
            flag=" ★100超" if rs/ns>=100 else ""
            print(f" ≥{k}: n={ns:>6}  ROI={rs/ns:>6.1f}%{flag}")
    print()

by_score(valid, "検証窓22-26 (out-of-sample・本番判定)")
by_score(learn, "学習窓14-21 (in-sample・参考=リーク込みの上限)")
