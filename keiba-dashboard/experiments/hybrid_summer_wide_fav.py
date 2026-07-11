"""夏3戦略の推奨馬 × 1番人気 のワイド 12年ROI。

- v2仕様(strategy_spec準拠)で推奨馬を再構成(2014-2025・稼働窓内)
- 各推奨馬について、そのレースの1番人気とのワイド1点(100円)を購入
- 推奨馬自身が1番人気の場合はスキップ(ワイド不成立)
- ワイド配当は experiments/payouts_v2races_2014_2026.tsv (既取得・オフライン)から引く
- (日付,場所,R)→netkeiba race_id は親CSVから対応表を構築(box2と同方式)
- 単勝版(v2_three_strats_roi)との比較・最高配当除外・プラス年も出す

実行: cd keiba-dashboard && python3 experiments/hybrid_summer_wide_fav.py
"""
import sqlite3
import sys
import gzip
import re
import os
from collections import defaultdict

sys.path.insert(0, ".")
from bs4 import BeautifulSoup
from live.sire_lineage_map import lineage_of
from live import strategy_spec as spec

DB = "keiba.db"
CSVS = ["../Data_2010.csv", "../Data_2020_fixed.csv"]
ARCHIVE = "state/result_html_archive"


def wide_payouts_archive(rid):
    """アーカイブ(gz)から ワイド {frozenset(pair): pay}。無ければ None。"""
    path = os.path.join(ARCHIVE, rid[:4], f"{rid}.html.gz")
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        soup = BeautifulSoup(fh.read(), "html.parser")
    out = {}
    for tr in soup.select("tr"):
        th = tr.find("th")
        if not th or "ワイド" not in th.get_text(strip=True):
            continue
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        nums = re.findall(r"\d+", tds[0].get_text(" ", strip=True))
        pays = re.findall(r"([\d,]+)円", tds[1].get_text(" ", strip=True))
        for i, pay in enumerate(pays):
            if 2 * i + 1 < len(nums):
                out[frozenset({int(nums[2 * i]), int(nums[2 * i + 1])})] = int(pay.replace(",", ""))
    return out if out else None

con = sqlite3.connect(DB)
career = defaultdict(list)
for horse, date in con.execute("select horse, date from entries where date is not null"):
    career[horse].append(date)
for d in career.values():
    d.sort()

def prior(h, dt):
    return sum(1 for d in career[h] if d < dt)

# picks: (date,venue,rno) -> list of (umaban, strat, year, win_odds, finish, win_payout)
picks = defaultdict(list)

lo, hi = spec.WINDOWS["shiba"]
for date, ven, rno, num, h, y, s, od, f, p in con.execute(
    f"""select date, venue, race_num, number, horse, year, sire, win_odds, finish, win_payout from entries
    where surface='芝' and class='未勝利' and gender='牝' and age=3
      and substr(date,6,5) between '{lo}' and '{hi}'
      and win_odds>=? and win_odds<? and year between 2014 and 2025 and finish is not null
      and number is not null""", spec.SHIBA_BAND):
    if (lineage_of(s or "") or "?") in spec.SHIBA_BLOOD and prior(h, date) >= spec.MIN_CAREER:
        picks[(date, ven, int(rno))].append((int(num), "芝", int(y), od, f, p))

lo, hi = spec.WINDOWS["dirt"]
cls = ",".join(f"'{c}'" for c in spec.DIRT_CLS)
for date, ven, rno, num, h, y, s, od, f, p in con.execute(
    f"""select date, venue, race_num, number, horse, year, sire, win_odds, finish, win_payout from entries
    where surface='ダ' and distance<={spec.DIRT_MAX_DIST} and class in ({cls})
      and gender='牝' and age=3 and substr(date,6,5) between '{lo}' and '{hi}'
      and win_odds>=? and win_odds<? and year between 2014 and 2025 and finish is not null
      and number is not null""", spec.DIRT_BAND):
    if (lineage_of(s or "") or "?") in spec.DIRT_BLOOD and prior(h, date) >= spec.MIN_CAREER:
        picks[(date, ven, int(rno))].append((int(num), "ダ", int(y), od, f, p))

lo, hi = spec.WINDOWS["shinba"]
sires = ",".join(f"'{s}'" for s in spec.SHINBA_SIRES)
for date, ven, rno, num, y, od, f, p in con.execute(
    f"""select date, venue, race_num, number, year, win_odds, finish, win_payout from entries
    where surface='芝' and class='新馬' and age=2 and sire in ({sires})
      and substr(date,6,5) between '{lo}' and '{hi}'
      and year between 2014 and 2025 and finish is not null and win_odds is not null
      and number is not null"""):
    picks[(date, ven, int(rno))].append((int(num), "新", int(y), od, f, p))

# 各pickレースの1番人気の馬番とオッズ
fav = {}        # (date,ven,rno) -> umaban
fav_odds = {}   # (date,ven,rno) -> win_odds
for date, ven, rno, num, od in con.execute(
    "select date, venue, race_num, number, win_odds from entries where popularity=1 and number is not null"):
    fav[(date, ven, int(rno))] = int(num)
    fav_odds[(date, ven, int(rno))] = od
con.close()

# (date,venue,rno) -> netkeiba race_id
def build_rid_map(needed):
    remain = set(needed); out = {}
    for path in CSVS:
        if not remain:
            break
        try:
            fh = open(path, "rb")
        except FileNotFoundError:
            continue
        header = fh.readline().decode("cp932", errors="replace").strip().split(",")
        if "場所" not in header:
            fh.seek(0); header = fh.readline().decode("utf-8", errors="replace").strip().split(","); enc = "utf-8"
        else:
            enc = "cp932"
        idx = {c: i for i, c in enumerate(header)}
        i_id, i_ven, i_r = idx["レースID(新/馬番無)"], idx["場所"], idx["Ｒ"]
        for raw in fh:
            if not remain:
                break
            try:
                row = raw.decode(enc, errors="replace").strip().split(",")
                rid16 = row[i_id]
                if len(rid16) != 16 or not rid16.isdigit():
                    continue
                date = f"{rid16[:4]}-{rid16[4:6]}-{rid16[6:8]}"
                key = (date, row[i_ven], int(row[i_r]))
                if key in remain:
                    out[key] = rid16[:4] + rid16[8:]; remain.discard(key)
            except Exception:
                continue
        fh.close()
    return out, remain

ridmap, unmatched = build_rid_map(picks.keys())

# ワイド配当ソース = アーカイブ ∪ payouts_v2races (同一rid規約)
wide = {}
n_arch = 0
for key, rid in ridmap.items():
    wp = wide_payouts_archive(rid)
    if wp is not None:
        wide[rid] = wp; n_arch += 1
# payouts_v2races で穴埋め
n_pay = 0
with open("experiments/payouts_v2races_2014_2026.tsv", encoding="utf-8") as f:
    next(f)
    tmp = defaultdict(dict)
    for line in f:
        rid, bt, combo, ret = line.rstrip("\n").split("\t")
        if bt != "ワイド":
            continue
        a, b = combo.split("-")
        tmp[rid][frozenset({int(a), int(b)})] = int(ret)
for rid, wp in tmp.items():
    if rid not in wide:
        wide[rid] = wp; n_pay += 1

print(f"pickレース {len(picks)} / ID解決 {len(ridmap)} / 未解決 {len(unmatched)}")
print(f"ワイド配当: アーカイブ {n_arch} + payouts穴埋め {n_pay} = {len(wide)}rid\n")

# --- 集計 ---
# bets: strat -> list of (year, wide_ret, win_ret, is_fav_skip)
by_strat = defaultdict(list)
all_bets = []
skip_fav = 0
no_payout = 0
for key, plist in picks.items():
    rid = ridmap.get(key)
    fnum = fav.get(key)
    wp = wide.get(rid) if rid else None
    fodds = fav_odds.get(key)
    for (num, strat, y, od, f, p) in plist:
        if fnum is None or num == fnum:
            skip_fav += 1
            continue
        if wp is None:
            no_payout += 1
            continue
        wret = wp.get(frozenset({num, fnum}), 0)
        win = float(p) if f == 1 and p else 0.0
        by_strat[strat].append((y, wret, win, fodds, f))
        all_bets.append((y, wret, win, strat, fodds, f))

def report(name, bets):
    if not bets:
        print(f"{name}: 該当なし"); return
    n = len(bets)
    wh = sum(1 for b in bets if b[1] > 0)
    wroi = sum(b[1] for b in bets) / (n * 100) * 100
    sroi = sum(b[2] for b in bets) / (n * 100) * 100
    sh = sum(1 for b in bets if b[2] > 0)
    print(f"■ {name}  (n={n})")
    print(f"   ワイド: 的中{wh}({wh/n*100:.1f}%)  ROI {wroi:.1f}%")
    print(f"   単勝(参考): 的中{sh}({sh/n*100:.1f}%)  ROI {sroi:.1f}%")
    years = sorted({b[0] for b in bets})
    plus = 0; parts = []
    for yy in years:
        ys = [b for b in bets if b[0] == yy]
        yr = sum(b[1] for b in ys) / (len(ys) * 100) * 100
        plus += yr >= 100
        parts.append(f"{yy}:{yr:.0f}%({len(ys)})")
    print(f"   プラス年 {plus}/{len(years)}  " + " ".join(parts))
    tops = sorted((b[1] for b in bets), reverse=True)
    print(f"   最高配当 {tops[0]:.0f}円  1本除外ROI {sum(tops[1:])/((n-1)*100)*100:.1f}%  "
          f"3本除外ROI {sum(tops[3:])/((n-3)*100)*100:.1f}%\n")

print(f"(1番人気と一致でスキップ {skip_fav}件 / ワイド配当欠損 {no_payout}件)\n")
for s in ("芝", "ダ", "新"):
    report(f"{s}戦略 × 1番人気ワイド", by_strat[s])
report("3戦略合算 × 1番人気ワイド", all_bets)

# ── 年別クロス表 (ワイドROI / 単勝ROI / n) ──
def yearly_table(name, bets):
    print(f"\n【{name}】年別")
    print(f"{'年':>6}{'n':>6}{'ワイドROI':>11}{'単勝ROI':>10}{'ワイド的中':>11}")
    ys = sorted({b[0] for b in bets})
    for y in ys:
        row = [b for b in bets if b[0] == y]
        n = len(row)
        wroi = sum(b[1] for b in row) / (n * 100) * 100
        sroi = sum(b[2] for b in row) / (n * 100) * 100
        wh = sum(1 for b in row if b[1] > 0)
        print(f"{y:>6}{n:>6}{wroi:>10.1f}%{sroi:>9.1f}%{wh:>8}({wh/n*100:.0f}%)")
    n = len(bets)
    print(f"{'計':>6}{n:>6}{sum(b[1] for b in bets)/(n*100)*100:>10.1f}%{sum(b[2] for b in bets)/(n*100)*100:>9.1f}%")

yearly_table("3戦略合算 × 1番人気ワイド", all_bets)
for s in ("芝", "ダ", "新"):
    yearly_table(f"{s}戦略", by_strat[s])

# ── 1番人気オッズ帯別 (相方の本命が堅いほど良いか) ──
print("\n\n######## 1番人気オッズ帯 × ワイドROI ########")
def obucket(o):
    if o is None: return "不明"
    if o < 1.5: return "A 1.0-1.4(激堅)"
    if o < 2.0: return "B 1.5-1.9(堅)"
    if o < 3.0: return "C 2.0-2.9"
    if o < 5.0: return "D 3.0-4.9"
    return "E 5.0+(混戦)"
def roi_of(bets):
    n=len(bets);
    return (n, sum(b[1] for b in bets)/(n*100)*100, sum(b[2] for b in bets)/(n*100)*100,
            sum(1 for b in bets if b[1]>0)/n*100) if n else (0,0,0,0)

for scope, bets in [("3戦略合算", all_bets)] + [(f"{s}戦略", by_strat[s]) for s in ("芝","ダ","新")]:
    print(f"\n【{scope}】")
    print(f"{'1番人気オッズ':<18}{'n':>6}{'ワイドROI':>11}{'単勝ROI':>10}{'ワイド的中':>10}")
    for b in ["A 1.0-1.4(激堅)","B 1.5-1.9(堅)","C 2.0-2.9","D 3.0-4.9","E 5.0+(混戦)"]:
        sub=[x for x in bets if obucket(x[-2])==b]
        if sub:
            n,wr,sr,wh=roi_of(sub)
            flag=" ★" if wr>=120 else ""
            print(f"{b:<18}{n:>6}{wr:>10.1f}%{sr:>9.1f}%{wh:>8.0f}%{flag}")
    # 1倍台(A+B) まとめ
    sub=[x for x in bets if x[-2] is not None and x[-2]<2.0]
    if sub:
        n,wr,sr,wh=roi_of(sub)
        print(f"{'→ 1倍台(<2.0)計':<18}{n:>6}{wr:>10.1f}%{sr:>9.1f}%{wh:>8.0f}%")

# ── なぜ芝が悪いか: 推奨馬の着順分布とワイド配当構造 ──
print("\n\n######## 推奨馬の性質診断 (なぜ芝はワイドが悪いか) ########")
print(f"{'戦略':<6}{'n':>6}{'勝率(1着)':>10}{'複勝率(top3)':>12}{'2-3着のみ':>10}{'ワイド的中':>10}{'勝時単勝均':>11}{'ワイド的中時均':>13}")
for s in ("芝","ダ","新"):
    bets=by_strat[s]; n=len(bets)
    win_n=sum(1 for b in bets if b[4]==1)
    top3=sum(1 for b in bets if b[4] in (1,2,3))
    place_only=sum(1 for b in bets if b[4] in (2,3))
    whit=[b[1] for b in bets if b[1]>0]
    winpays=[b[2] for b in bets if b[2]>0]
    avg_win=sum(winpays)/len(winpays) if winpays else 0
    avg_wide=sum(whit)/len(whit) if whit else 0
    print(f"{s:<6}{n:>6}{win_n/n*100:>9.1f}%{top3/n*100:>11.1f}%{place_only/n*100:>9.1f}%"
          f"{len(whit)/n*100:>9.1f}%{avg_win:>10.0f}円{avg_wide:>11.0f}円")

# ── 新馬 × 1番人気1倍台 の精査 ──
print("\n\n######## 新馬戦略 × 1番人気1倍台(<2.0) 精査 ########")
sub = [b for b in by_strat["新"] if b[3] is not None and b[3] < 2.0]
n = len(sub)
hits = [b for b in sub if b[1] > 0]
tot = sum(b[1] for b in sub)
print(f"n={n}  ワイド的中{len(hits)}({len(hits)/n*100:.0f}%)  ROI {tot/(n*100)*100:.1f}%")
print(f"的中配当の内訳(円): {sorted((int(b[1]) for b in hits), reverse=True)}")
tops = sorted((b[1] for b in sub), reverse=True)
print(f"最高1本除外ROI {sum(tops[1:])/((n-1)*100)*100:.1f}%  上位3本除外ROI {sum(tops[3:])/((n-3)*100)*100:.1f}%")
print("\n【年別】")
print(f"{'年':>6}{'n':>5}{'的中':>5}{'ワイドROI':>11}{'配当(円)':>20}")
for y in sorted({b[0] for b in sub}):
    row = [b for b in sub if b[0] == y]
    hh = [int(b[1]) for b in row if b[1] > 0]
    print(f"{y:>6}{len(row):>5}{len(hh):>5}{sum(b[1] for b in row)/(len(row)*100)*100:>10.1f}%{str(sorted(hh, reverse=True)):>20}")
print("\n【激堅A(1.0-1.4) vs 堅B(1.5-1.9)】")
for lab, lo, hi in [("A 1.0-1.4", 0, 1.5), ("B 1.5-1.9", 1.5, 2.0)]:
    ss = [b for b in sub if lo <= b[3] < hi]
    if ss:
        hh = [int(b[1]) for b in ss if b[1] > 0]
        print(f"{lab}: n={len(ss)} 的中{len(hh)}({len(hh)/len(ss)*100:.0f}%) ROI {sum(b[1] for b in ss)/(len(ss)*100)*100:.1f}% 配当{sorted(hh, reverse=True)}")
