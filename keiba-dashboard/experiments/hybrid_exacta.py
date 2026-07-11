"""夏3戦略の推奨馬×1番人気を馬単で。12年。
- 推奨馬→1人気 / 1人気→推奨馬 / 表裏(両方) の3パターン
- 馬単配当: アーカイブ ∪ df_race_return.csv(2024-25)。単勝は基準としてdbから
実行: cd keiba-dashboard && python3 experiments/hybrid_exacta.py
"""
import sqlite3, sys, gzip, re, os
from collections import defaultdict
sys.path.insert(0, ".")
from bs4 import BeautifulSoup
from live.sire_lineage_map import lineage_of
from live import strategy_spec as spec

DB = "keiba.db"; CSVS = ["../Data_2010.csv", "../Data_2020_fixed.csv"]; ARCHIVE = "state/result_html_archive"
DFRET = "/Users/yamadori/keiba_data_src/common/data/df_csv/df_race_return.csv"
con = sqlite3.connect(DB)
career = defaultdict(list)
for h, d in con.execute("select horse, date from entries where date is not null"):
    career[h].append(d)
for d in career.values():
    d.sort()
def prior(h, dt): return sum(1 for d in career[h] if d < dt)

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

pop2uma = defaultdict(dict); keyset = set(picks.keys()); winpay = defaultdict(dict); finmap = defaultdict(dict)
for date, ven, rno, num, popu, wpay, fin in con.execute(
    "select date,venue,race_num,number,popularity,win_payout,finish from entries where number is not null"):
    k = (date, ven, int(rno))
    if k not in keyset: continue
    if popu is not None: pop2uma[k][int(popu)] = int(num)
    winpay[k][int(num)] = wpay; finmap[k][int(num)] = fin
con.close()

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
        idx = {c: i for i, c in enumerate(header)}; i_id, i_ven, i_r = idx["レースID(新/馬番無)"], idx["場所"], idx["Ｒ"]
        for raw in fh:
            if not remain: break
            try:
                row = raw.decode(enc, "replace").strip().split(","); rid16 = row[i_id]
                if len(rid16) != 16 or not rid16.isdigit(): continue
                key = (f"{rid16[:4]}-{rid16[4:6]}-{rid16[6:8]}", row[i_ven], int(row[i_r]))
                if key in remain: out[key] = rid16[:4] + rid16[8:]; remain.discard(key)
            except Exception: continue
        fh.close()
    return out
ridmap = build_rid_map(picks.keys())

def madan_from_archive(rid):
    path = os.path.join(ARCHIVE, rid[:4], f"{rid}.html.gz")
    if not os.path.exists(path): return None
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        soup = BeautifulSoup(fh.read(), "html.parser")
    for tr in soup.select("tr"):
        th = tr.find("th")
        if not th or th.get_text(strip=True) != "馬単": continue
        tds = tr.find_all("td")
        if len(tds) < 2: continue
        nums = re.findall(r"\d+", tds[0].get_text(" ", strip=True)); pays = re.findall(r"([\d,]+)円", tds[1].get_text(" ", strip=True))
        if len(nums) >= 2 and pays:
            return {(int(nums[0]), int(nums[1])): int(pays[0].replace(",", ""))}
    return None
madan = {}
for k, rid in ridmap.items():
    m = madan_from_archive(rid)
    if m: madan[rid] = m
# df_race_return で穴埋め(2024-25)
tmp = defaultdict(dict)
with open(DFRET, encoding="utf-8") as f:
    next(f)
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) < 4 or p[1] != "馬単": continue
        try:
            a, b = p[2].split("-"); tmp[p[0]][(int(a), int(b))] = int(float(p[3]))
        except Exception: continue
for r, m in tmp.items():
    madan.setdefault(r, m)

acc = {m: defaultdict(lambda: [0, 0.0]) for m in ["単勝", "馬単 推→1人気", "馬単 1人気→推", "馬単 表裏"]}
cov = miss = 0
for key, plist in picks.items():
    rid = ridmap.get(key); md = madan.get(rid); fav = pop2uma.get(key, {}).get(1)
    if md is None: miss += len(plist)
    else: cov += len(plist)
    for (u, s, y) in plist:
        wpay = winpay.get(key, {}).get(u); fin = finmap.get(key, {}).get(u)
        acc["単勝"][s][0] += 100; acc["単勝"][s][1] += float(wpay) if fin == 1 and wpay else 0
        if md is None or fav is None or u == fav: continue
        pf = md.get((u, fav), 0); fp = md.get((fav, u), 0)
        acc["馬単 推→1人気"][s][0] += 100; acc["馬単 推→1人気"][s][1] += pf
        acc["馬単 1人気→推"][s][0] += 100; acc["馬単 1人気→推"][s][1] += fp
        acc["馬単 表裏"][s][0] += 200; acc["馬単 表裏"][s][1] += pf + fp

print(f"馬単配当カバー: {cov}/{cov+miss} pick  (アーカイブ∪df_race_return)\n")
print(f"{'方式':<16}{'芝':>13}{'ダ':>13}{'新':>13}{'合算':>15}")
for m, a in acc.items():
    cells = []; tn = tr_ = 0
    for s in ("芝", "ダ", "新"):
        n, r = a[s]; tn += n; tr_ += r
        cells.append(f"{r/n*100:.0f}%(n{n//100})" if n else "-")
    cells.append(f"{tr_/tn*100:.1f}%" if tn else "-")
    print(f"{m:<16}{cells[0]:>13}{cells[1]:>13}{cells[2]:>13}{cells[3]:>15}")
