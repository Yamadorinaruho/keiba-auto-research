"""【総決算・全カバレッジ版】夏3戦略の推奨馬を全券種・全構成で12年比較。
配当 = all_payouts_2010_2026.tsv ∪ アーカイブgz(欠落分を全券種補完)。
実行: cd keiba-dashboard && python3 experiments/hybrid_allbets_final.py
"""
import sqlite3, sys, itertools, gzip, os, re
from collections import defaultdict
sys.path.insert(0, ".")
from bs4 import BeautifulSoup
from live.sire_lineage_map import lineage_of
from live.netkeiba_scraper import fetch
from live import strategy_spec as spec

DB = "keiba.db"; PAY = "experiments/all_payouts_2010_2026.tsv"
CSVS = ["../Data_2010.csv", "../Data_2020_fixed.csv"]; ARCHIVE = "state/result_html_archive"
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

pop2uma = defaultdict(dict); keyset = set(picks.keys())
for date, ven, rno, num, popu in con.execute(
    "select date,venue,race_num,number,popularity from entries where number is not null and popularity is not null"):
    k = (date, ven, int(rno))
    if k in keyset:
        pop2uma[k][int(popu)] = int(num)
con.close()

# 1) all_payouts をロード
pay = defaultdict(lambda: defaultdict(dict))
with open(PAY, encoding="utf-8") as f:
    next(f)
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) < 7 or not p[3].isdigit(): continue
        k = (p[1], p[2], int(p[3]))
        if k not in keyset: continue
        try: pay[k][p[4]][p[5]] = int(p[6])
        except ValueError: continue
cov_ap = sum(1 for k in picks if k in pay)

# 2) ridmap (欠落キーだけ)
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

missing = [k for k in picks if k not in pay]
ridmap = build_rid_map(missing)

# 3) アーカイブgzから全券種を補完
BT = {"単勝": "単勝", "複勝": "複勝", "枠連": "枠連", "馬連": "馬連", "ワイド": "ワイド",
      "馬単": "馬単", "3連複": "3連複", "三連複": "3連複", "3連単": "3連単", "三連単": "3連単"}
def parse_payout_html(html):
    soup = BeautifulSoup(html, "html.parser")
    out = defaultdict(dict)
    for tr in soup.select("tr"):
        th = tr.find("th")
        if not th: continue
        lbl = th.get_text(strip=True)
        bt = BT.get(lbl)
        if not bt: continue
        tds = tr.find_all("td")
        if len(tds) < 2: continue
        nums = re.findall(r"\d+", tds[0].get_text(" ", strip=True))
        pays = re.findall(r"([\d,]+)円", tds[1].get_text(" ", strip=True))
        if bt in ("単勝",):
            if nums and pays: out[bt][nums[0]] = int(pays[0].replace(",", ""))
        elif bt == "複勝":
            for i, p in enumerate(pays):
                if i < len(nums): out[bt][nums[i]] = int(p.replace(",", ""))
        elif bt in ("枠連", "馬連", "ワイド"):
            for i, p in enumerate(pays):
                if 2*i+1 < len(nums):
                    a, b = nums[2*i], nums[2*i+1]
                    out[bt]["-".join(str(x) for x in sorted((int(a), int(b))))] = int(p.replace(",", ""))
        elif bt == "3連複":
            if len(nums) >= 3 and pays:
                out[bt]["-".join(str(x) for x in sorted(int(n) for n in nums[:3]))] = int(pays[0].replace(",", ""))
        elif bt == "馬単":
            if len(nums) >= 2 and pays: out[bt][f"{nums[0]}-{nums[1]}"] = int(pays[0].replace(",", ""))
        elif bt == "3連単":
            if len(nums) >= 3 and pays: out[bt][f"{nums[0]}-{nums[1]}-{nums[2]}"] = int(pays[0].replace(",", ""))
    return out or None

def payouts_from_source(rid):
    """アーカイブgzがあれば使い、無ければnetkeibaを取得(キャッシュ)。"""
    path = os.path.join(ARCHIVE, rid[:4], f"{rid}.html.gz")
    if os.path.exists(path):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return parse_payout_html(fh.read())
    try:
        html = fetch(f"https://race.netkeiba.com/race/result.html?race_id={rid}",
                     cache_key=f"result_{rid}.html", force=False)
        return parse_payout_html(html)
    except Exception:
        return None

filled = 0
for i, k in enumerate(missing):
    rid = ridmap.get(k)
    if not rid: continue
    if i % 100 == 0:
        print(f"  補完 {i}/{len(missing)}...", flush=True)
    ar = payouts_from_source(rid)
    if ar:
        for bt, d in ar.items():
            pay[k][bt].update(d)
        filled += 1

covered = sum(1 for k in picks if k in pay)
print(f"pickレース {len(picks)}  |  all_payouts {cov_ap} + アーカイブ補完 {filled} = {covered} ({covered/len(picks)*100:.1f}%)\n")

def uni(*xs): return "-".join(str(x) for x in sorted(xs))
def ordr(*xs): return "-".join(str(x) for x in xs)
acc = {m: defaultdict(lambda: [0, 0.0, 0, 0]) for m in [   # [stake, ret, n_opp, hits]
    "単勝", "複勝", "ワイド×1人気", "ワイド×2人気", "ワイド×3人気", "ワイドBOX自",
    "馬連×1人気", "馬単 推→1人気", "馬単 1人気→推", "馬単 表裏",
    "3連複 推+1+2人気", "3連単 1→推→2人気"]}
returns_seq = defaultdict(list)   # 単点構成の 払戻/100 列 (Kelly用)
KELLY_TGT = {"単勝", "複勝", "ワイド×1人気", "馬連×1人気", "馬単 推→1人気"}
def add(m, s, stake, ret):
    a = acc[m][s]; a[0] += stake; a[1] += ret; a[2] += 1; a[3] += 1 if ret > 0 else 0
    if m in KELLY_TGT and stake == 100:
        returns_seq[m].append(ret / 100.0)

for key, plist in picks.items():
    P = pay.get(key)
    if not P: continue
    pm = pop2uma.get(key, {}); f1, f2, f3 = pm.get(1), pm.get(2), pm.get(3)
    ums = [u for (u, s, y) in plist]; strat = plist[0][1]
    for (u, s, y) in plist:
        add("単勝", s, 100, P.get("単勝", {}).get(str(u), 0))
        add("複勝", s, 100, P.get("複勝", {}).get(str(u), 0))
        for k, lab in [(f1, "ワイド×1人気"), (f2, "ワイド×2人気"), (f3, "ワイド×3人気")]:
            if k and k != u: add(lab, s, 100, P.get("ワイド", {}).get(uni(u, k), 0))
        if f1 and f1 != u:
            add("馬連×1人気", s, 100, P.get("馬連", {}).get(uni(u, f1), 0))
            add("馬単 推→1人気", s, 100, P.get("馬単", {}).get(ordr(u, f1), 0))
            add("馬単 1人気→推", s, 100, P.get("馬単", {}).get(ordr(f1, u), 0))
            add("馬単 表裏", s, 200, P.get("馬単", {}).get(ordr(u, f1), 0) + P.get("馬単", {}).get(ordr(f1, u), 0))
        if f1 and f2 and len({u, f1, f2}) == 3:
            add("3連複 推+1+2人気", s, 100, P.get("3連複", {}).get(uni(u, f1, f2), 0))
            add("3連単 1→推→2人気", s, 100, P.get("3連単", {}).get(ordr(f1, u, f2), 0))
    if len(set(ums)) >= 2:
        for a, b in itertools.combinations(sorted(set(ums)), 2):
            add("ワイドBOX自", strat, 100, P.get("ワイド", {}).get(uni(a, b), 0))

print(f"{'券種・構成':<18}{'芝ROI':>8}{'ダROI':>8}{'新ROI':>8}{'合算ROI':>10}{'n':>7}{'的中':>7}{'的中率':>8}")
for m, a in acc.items():
    cells = []; ts = tr_ = tn = th = 0
    for s in ("芝", "ダ", "新"):
        stake, r, n, h = a[s]; ts += stake; tr_ += r; tn += n; th += h
        cells.append(f"{r/stake*100:.0f}%" if stake else "-")
    roi = f"{tr_/ts*100:.1f}%" if ts else "-"
    hr = f"{th/tn*100:.1f}%" if tn else "-"
    print(f"{m:<18}{cells[0]:>8}{cells[1]:>8}{cells[2]:>8}{roi:>10}{tn:>7}{th:>7}{hr:>8}")

# ── 複利(Kelly)成長率の比較 ──
import math
print("\n=== 複利成長の比較 (1点あたりの対数成長率・Kelly最適f) ===")
print(f"{'券種':<14}{'ROI':>7}{'Kelly f*':>9}{'成長/点':>10}{'成長/1000点':>13}{'破産寄り':>8}")
def ggrow(returns, f):
    nets = [r - 1 for r in returns]; g = 0.0
    for x in nets:
        v = 1 + f * x
        if v <= 1e-9: return None
        g += math.log(v)
    return g / len(nets)
def kelly(returns):
    best_f, best_g = 0.0, 0.0; f = 0.0
    while f <= 1.0:
        g = ggrow(returns, f)
        if g is not None and g > best_g: best_g, best_f = g, f
        f += 0.001
    return best_f, best_g
for m in ["単勝", "複勝", "ワイド×1人気", "馬連×1人気", "馬単 推→1人気"]:
    R = returns_seq.get(m, [])
    if not R: continue
    roi = sum(R) / len(R) * 100
    f_, g = kelly(R)
    per1000 = (math.exp(g * 1000) - 1) * 100 if g * 1000 < 20 else float('inf')
    warn = "×" if roi < 100.5 else ("△" if roi < 103 else "")
    p1000 = "∞超" if per1000 == float('inf') else f"{per1000:+.0f}%"
    print(f"{m:<14}{roi:>6.1f}%{f_:>8.1%}{g:>10.5f}{p1000:>13}{warn:>8}")

# 単勝の f 感度カーブ + 勝ち配当分布
R = returns_seq["単勝"]
wins = sorted((r for r in R if r > 0), reverse=True)
print("\n=== 単勝: 賭け率f 感度 (1000点あたり複利リターン) ===")
for f in [0.005, 0.01, 0.015, 0.02, 0.03, 0.05, 0.10, 0.20]:
    g = ggrow(R, f)
    p = (math.exp(g*1000)-1)*100 if g and g*1000 < 30 else (float('inf') if g and g>0 else -100)
    ps = "∞超" if p == float('inf') else f"{p:+.0f}%"
    print(f"  f={f:>5.1%}: 成長/点 {g:>9.5f}  1000点 {ps}")
print(f"単勝 勝ち{len(wins)}本 配当(倍): 最高{max(wins)/1:.0f} 上位[{','.join(f'{w:.0f}' for w in wins[:6])}] 中央{sorted(wins)[len(wins)//2]:.0f}")
