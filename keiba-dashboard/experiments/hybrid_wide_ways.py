"""ワイドで勝つ形を探す。夏3戦略の推奨馬を、相手/券種を変えて12年比較。
- 単勝(基準) / 複勝 / ワイド×1人気 / ×2人気 / ×3人気 / 推奨馬同士ワイドBOX
- 配当: 単勝複勝=db, ワイド=アーカイブ∪payouts_v2races
実行: cd keiba-dashboard && python3 experiments/hybrid_wide_ways.py
"""
import sqlite3, sys, gzip, re, os, itertools
from collections import defaultdict
sys.path.insert(0, ".")
from bs4 import BeautifulSoup
from live.sire_lineage_map import lineage_of
from live import strategy_spec as spec

DB = "keiba.db"; CSVS = ["../Data_2010.csv", "../Data_2020_fixed.csv"]; ARCHIVE = "state/result_html_archive"
con = sqlite3.connect(DB)
career = defaultdict(list)
for h, d in con.execute("select horse, date from entries where date is not null"):
    career[h].append(d)
for d in career.values():
    d.sort()
def prior(h, dt): return sum(1 for d in career[h] if d < dt)

# picks: (date,ven,rno) -> [(umaban, strat, year)]
picks = defaultdict(list)
lo, hi = spec.WINDOWS["shiba"]
for date, ven, rno, num, h, y, s in con.execute(
    f"""select date,venue,race_num,number,horse,year,sire from entries where surface='芝' and class='未勝利'
    and gender='牝' and age=3 and substr(date,6,5) between '{lo}' and '{hi}' and win_odds>=? and win_odds<?
    and year between 2014 and 2025 and finish is not null and number is not null""", spec.SHIBA_BAND):
    if (lineage_of(s or "") or "?") in spec.SHIBA_BLOOD and prior(h, date) >= spec.MIN_CAREER:
        picks[(date, ven, int(rno))].append((int(num), "芝", int(y)))
lo, hi = spec.WINDOWS["dirt"]; cls = ",".join(f"'{c}'" for c in spec.DIRT_CLS)
for date, ven, rno, num, h, y, s in con.execute(
    f"""select date,venue,race_num,number,horse,year,sire from entries where surface='ダ' and distance<={spec.DIRT_MAX_DIST}
    and class in ({cls}) and gender='牝' and age=3 and substr(date,6,5) between '{lo}' and '{hi}' and win_odds>=? and win_odds<?
    and year between 2014 and 2025 and finish is not null and number is not null""", spec.DIRT_BAND):
    if (lineage_of(s or "") or "?") in spec.DIRT_BLOOD and prior(h, date) >= spec.MIN_CAREER:
        picks[(date, ven, int(rno))].append((int(num), "ダ", int(y)))
lo, hi = spec.WINDOWS["shinba"]; sires = ",".join(f"'{s}'" for s in spec.SHINBA_SIRES)
for date, ven, rno, num, y in con.execute(
    f"""select date,venue,race_num,number,year from entries where surface='芝' and class='新馬' and age=2
    and sire in ({sires}) and substr(date,6,5) between '{lo}' and '{hi}' and year between 2014 and 2025
    and finish is not null and win_odds is not null and number is not null"""):
    picks[(date, ven, int(rno))].append((int(num), "新", int(y)))

# db: 各pickレースの 人気→馬番, 馬番→(単勝配当,複勝配当,着順)
pop2uma = defaultdict(dict); uma_pay = defaultdict(dict)
keyset = set(picks.keys())
for date, ven, rno, num, popu, wpay, ppay, fin in con.execute(
    "select date,venue,race_num,number,popularity,win_payout,place_payout,finish from entries where number is not null"):
    k = (date, ven, rno if isinstance(rno, int) else int(rno))
    if k not in keyset:
        continue
    if popu is not None:
        pop2uma[k][int(popu)] = int(num)
    uma_pay[k][int(num)] = (wpay, ppay, fin)
con.close()

# (date,ven,rno)->netkeiba rid
def build_rid_map(needed):
    remain = set(needed); out = {}
    for path in CSVS:
        if not remain: break
        try: fh = open(path, "rb")
        except FileNotFoundError: continue
        header = fh.readline().decode("cp932", "replace").strip().split(",")
        if "場所" not in header:
            fh.seek(0); header = fh.readline().decode("utf-8", "replace").strip().split(","); enc = "utf-8"
        else: enc = "cp932"
        idx = {c: i for i, c in enumerate(header)}
        i_id, i_ven, i_r = idx["レースID(新/馬番無)"], idx["場所"], idx["Ｒ"]
        for raw in fh:
            if not remain: break
            try:
                row = raw.decode(enc, "replace").strip().split(",")
                rid16 = row[i_id]
                if len(rid16) != 16 or not rid16.isdigit(): continue
                key = (f"{rid16[:4]}-{rid16[4:6]}-{rid16[6:8]}", row[i_ven], int(row[i_r]))
                if key in remain: out[key] = rid16[:4] + rid16[8:]; remain.discard(key)
            except Exception: continue
        fh.close()
    return out
ridmap = build_rid_map(picks.keys())

def wide_from_archive(rid):
    path = os.path.join(ARCHIVE, rid[:4], f"{rid}.html.gz")
    if not os.path.exists(path): return None
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        soup = BeautifulSoup(fh.read(), "html.parser")
    out = {}
    for tr in soup.select("tr"):
        th = tr.find("th")
        if not th or "ワイド" not in th.get_text(strip=True): continue
        tds = tr.find_all("td")
        if len(tds) < 2: continue
        nums = re.findall(r"\d+", tds[0].get_text(" ", strip=True)); pays = re.findall(r"([\d,]+)円", tds[1].get_text(" ", strip=True))
        for i, p in enumerate(pays):
            if 2*i+1 < len(nums): out[frozenset({int(nums[2*i]), int(nums[2*i+1])})] = int(p.replace(",", ""))
    return out or None
wide = {}
for k, rid in ridmap.items():
    wp = wide_from_archive(rid)
    if wp: wide[rid] = wp
tmp = defaultdict(dict)
for line in open("experiments/payouts_v2races_2014_2026.tsv", encoding="utf-8").read().splitlines()[1:]:
    r, bt, combo, ret = line.split("\t")
    if bt == "ワイド":
        a, b = combo.split("-"); tmp[r][frozenset({int(a), int(b)})] = int(ret)
for r, wp in tmp.items():
    wide.setdefault(r, wp)

# ---- 各方式のROI ----
# 戦略別に (n, ret) を積む
def newacc(): return defaultdict(lambda: [0, 0.0])
methods = {m: newacc() for m in ["単勝", "複勝", "ワ×1人気", "ワ×2人気", "ワ×3人気", "ワBOX(自)"]}

for key, plist in picks.items():
    rid = ridmap.get(key); wp = wide.get(rid); pm = pop2uma.get(key, {}); up = uma_pay.get(key, {})
    ums = [u for (u, s, y) in plist]
    strat = plist[0][1]; yr = plist[0][2]
    for (u, s, y) in plist:
        wpay, ppay, fin = up.get(u, (None, None, None))
        # 単勝・複勝
        methods["単勝"][s][0] += 100; methods["単勝"][s][1] += float(wpay) if fin == 1 and wpay else 0
        methods["複勝"][s][0] += 100; methods["複勝"][s][1] += float(ppay) if ppay else 0
        # ワイド × k番人気
        for k, lab in [(1, "ワ×1人気"), (2, "ワ×2人気"), (3, "ワ×3人気")]:
            partner = pm.get(k)
            if partner is None or partner == u or wp is None:
                continue
            methods[lab][s][0] += 100; methods[lab][s][1] += wp.get(frozenset({u, partner}), 0)
    # 推奨馬同士BOX (2頭以上)
    if len(ums) >= 2 and wp is not None:
        for a, b in itertools.combinations(sorted(set(ums)), 2):
            methods["ワBOX(自)"][strat][0] += 100; methods["ワBOX(自)"][strat][1] += wp.get(frozenset({a, b}), 0)

print("方式別 12年ROI (戦略別 / 合算)  ※単複=db, ワイド=アーカイブ∪payouts\n")
print(f"{'方式':<12}{'芝':>14}{'ダ':>14}{'新':>14}{'合算':>16}")
for m, acc in methods.items():
    cells = []
    tn = tr_ = 0
    for s in ("芝", "ダ", "新"):
        n, r = acc[s]; tn += n; tr_ += r
        cells.append(f"{r/n*100:.0f}%(n{n//100})" if n else "-")
    cells.append(f"{tr_/tn*100:.1f}%(n{tn//100})" if tn else "-")
    print(f"{m:<12}{cells[0]:>14}{cells[1]:>14}{cells[2]:>14}{cells[3]:>16}")
