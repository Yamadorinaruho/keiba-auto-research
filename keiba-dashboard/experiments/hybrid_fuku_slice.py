#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""複勝◎(1番人気)を各軸で単変量スライス。100%超えの方向を探す。
各セルで n / 素ROI / 2024ROI / 2025ROI を併記(両年チェック)。"""
import csv
from collections import defaultdict

BASE = "/Users/yamadori/keiba_data_src/common/data/df_csv/"

# race情報
info = {}
with open(BASE + "df_race_info.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f, delimiter="\t"):
        info[r["race_id"]] = r

# ◎(1番人気)の属性 + 頭数
hon = {}       # rid -> dict(odds,rank,sex,age,impost,wdiff,waku,uma)
field_n = {}
race_horses = defaultdict(dict)
with open(BASE + "df_race.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        rid = row["race_id"]
        try:
            pop = int(float(row["popularity"])); rank = int(float(row["rank"])); uma = int(float(row["umaban"]))
        except (ValueError, TypeError):
            continue
        race_horses[rid][uma] = pop
        if pop == 1:
            try:
                hon[rid] = dict(
                    odds=float(row["tansho_odds"]), rank=rank, uma=uma,
                    sex=row["sex"], age=int(float(row["age"])),
                    impost=float(row["impost"]) if row["impost"] else None,
                    wdiff=int(float(row["weight_diff"])) if row["weight_diff"] not in ("", "None") else None,
                    waku=int(float(row["wakuban"])) if row["wakuban"] else None,
                )
            except (ValueError, TypeError):
                pass
for rid, d in race_horses.items():
    field_n[rid] = len(d)

# 複勝配当
fuku = defaultdict(dict)
with open(BASE + "df_race_return.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        if row["bet_type"] != "複勝":
            continue
        try:
            fuku[row["race_id"]][int(row["win_umaban"])] = int(float(row["return"]))
        except (ValueError, TypeError):
            continue

# 対象: ◎属性・複勝・info がそろうレース
rids = [r for r in hon if r in fuku and r in info]

def payout(rid):
    return fuku[rid].get(hon[rid]["uma"], 0)

def agg(subset):
    s = defaultdict(int); rr = defaultdict(int); n = 0
    ts = tr = 0
    for rid in subset:
        p = payout(rid)
        y = info[rid]["date"][:4]
        s[y] += 100; rr[y] += p
        ts += 100; tr += p; n += 1
    roi = tr/ts*100 if ts else 0
    r24 = rr["2024"]/s["2024"]*100 if s["2024"] else 0
    r25 = rr["2025"]/s["2025"]*100 if s["2025"] else 0
    return n, roi, r24, r25

def report(title, buckets):
    # buckets: list of (label, [rids])
    print(f"=== {title} ===")
    print(f"{'セル':<18}{'n':>7}{'ROI':>8}{'24':>8}{'25':>8}")
    for label, sub in buckets:
        if not sub:
            continue
        n, roi, r24, r25 = agg(sub)
        mark = " ★100超" if roi >= 100 else (" ○95+" if roi >= 95 else "")
        print(f"{label:<18}{n:>7}{roi:>7.1f}%{r24:>7.0f}%{r25:>7.0f}%{mark}")
    print()

print(f"母集団: 複勝◎ 全{len(rids)}R  素ROI {agg(rids)[1]:.1f}%\n")

# --- 軸1: ◎の単勝オッズ帯(favorite-longshot core) ---
odds_bins = [(0,1.5),(1.5,2.0),(2.0,2.5),(2.5,3.0),(3.0,4.0),(4.0,6.0),(6.0,99)]
report("軸1 ◎単勝オッズ帯", [(f"{a}-{b}倍", [r for r in rids if a<=hon[r]['odds']<b]) for a,b in odds_bins])

# --- 軸2: 頭数 ---
report("軸2 出走頭数", [
    ("〜9頭", [r for r in rids if field_n[r]<=9]),
    ("10-12頭", [r for r in rids if 10<=field_n[r]<=12]),
    ("13-15頭", [r for r in rids if 13<=field_n[r]<=15]),
    ("16頭〜", [r for r in rids if field_n[r]>=16]),
])

# --- 軸3: 芝/ダ (race_type 1=芝?2=ダ?) ---
report("軸3 race_type", [(f"type={t}", [r for r in rids if info[r]['race_type']==t]) for t in sorted(set(info[r]['race_type'] for r in rids))])

# --- 軸4: 距離帯 ---
def clen(r):
    try: return int(float(info[r]['course_len']))
    except: return 0
report("軸4 距離帯", [
    ("〜1300", [r for r in rids if 0<clen(r)<=1300]),
    ("1301-1600", [r for r in rids if 1300<clen(r)<=1600]),
    ("1601-2000", [r for r in rids if 1600<clen(r)<=2000]),
    ("2001〜", [r for r in rids if clen(r)>2000]),
])

# --- 軸5: race_class ---
report("軸5 race_class", [(f"class={c}", [r for r in rids if info[r]['race_class']==c]) for c in sorted(set(info[r]['race_class'] for r in rids))])

# --- 軸6: 馬場状態 ---
report("軸6 ground_state", [(f"gs={g}", [r for r in rids if info[r]['ground_state']==g]) for g in sorted(set(info[r]['ground_state'] for r in rids))])

# --- 軸7: 競馬場 place ---
report("軸7 place", [(f"place={p}", [r for r in rids if info[r]['place']==p]) for p in sorted(set(info[r]['place'] for r in rids))])

# --- 軸8: ◎馬体重増減 ---
report("軸8 ◎馬体重増減", [
    ("大減-6以下", [r for r in rids if hon[r]['wdiff'] is not None and hon[r]['wdiff']<=-6]),
    ("減-5〜-1", [r for r in rids if hon[r]['wdiff'] is not None and -5<=hon[r]['wdiff']<=-1]),
    ("増減0", [r for r in rids if hon[r]['wdiff']==0]),
    ("増+1〜+5", [r for r in rids if hon[r]['wdiff'] is not None and 1<=hon[r]['wdiff']<=5]),
    ("大増+6以上", [r for r in rids if hon[r]['wdiff'] is not None and hon[r]['wdiff']>=6]),
])

# --- 軸9: ◎性別 ---
report("軸9 ◎性別", [(f"sex={s}", [r for r in rids if hon[r]['sex']==s]) for s in sorted(set(hon[r]['sex'] for r in rids))])

# --- 軸10: ◎年齢 ---
report("軸10 ◎年齢", [(f"{a}歳", [r for r in rids if hon[r]['age']==a]) for a in sorted(set(hon[r]['age'] for r in rids))])
