"""2026フォワード: 夏戦略(ダ・新)の推奨馬×1番人気ワイドを今季分で試す。
- ピックは state/summer_sched_2026*.json の picks(確定買い目)から(race_id=netkeiba・odds_pre付き)
- 1番人気/着順/配当は netkeiba結果ページから取得
- ※今季は稼働開始~3週間・n極小。参考(anecdote)であり判定材料にはならない。
実行: cd keiba-dashboard && python3 experiments/hybrid_summer_wide_2026.py
"""
import sys, glob, json, re
sys.path.insert(0, ".")
from bs4 import BeautifulSoup
from live.netkeiba_scraper import fetch

# --- 2026 picks を state から集約(race_idごとに最新ファイルで上書き) ---
STRATS = ("shiba", "dirt", "shinba")
races = {}   # race_id -> {strat, date, venue, rno, picks:[umaban...]}
for f in sorted(glob.glob("state/summer_sched_2026*.json")):
    dd = json.load(open(f))
    for rr in dd.get("races", []):
        if rr.get("strat") not in STRATS:
            continue
        pk = rr.get("picks")
        if not pk:
            continue
        races[rr["race_id"]] = {
            "strat": rr["strat"], "date": dd["date"], "venue": rr["venue"],
            "rno": rr["rno"], "picks": [p["umaban"] for p in pk],
        }

def parse_result(rid):
    html = fetch(f"https://race.netkeiba.com/race/result.html?race_id={rid}",
                 cache_key=f"result_{rid}.html", force=False)
    soup = BeautifulSoup(html, "html.parser")
    fin = {}    # umaban -> finish
    pop = {}    # umaban -> popularity
    for tr in soup.select("tr"):
        c = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(c) >= 11 and c[0].isdigit() and c[2].isdigit():
            uma = int(c[2])
            try:
                fin[uma] = int(c[0])
            except ValueError:
                pass
            if len(c) > 9 and c[9].isdigit():
                pop[uma] = int(c[9])
    # 配当
    wide = {}; tan = {}
    for tr in soup.select("tr"):
        th = tr.find("th")
        if not th:
            continue
        lbl = th.get_text(strip=True)
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        nums = re.findall(r"\d+", tds[0].get_text(" ", strip=True))
        pays = re.findall(r"([\d,]+)円", tds[1].get_text(" ", strip=True))
        if "ワイド" in lbl:
            for i, p in enumerate(pays):
                if 2 * i + 1 < len(nums):
                    wide[frozenset({int(nums[2*i]), int(nums[2*i+1])})] = int(p.replace(",", ""))
        elif lbl == "単勝":
            if nums and pays:
                tan[int(nums[0])] = int(pays[0].replace(",", ""))
    fav = next((u for u, p in pop.items() if p == 1), None)
    return fin, fav, wide, tan

# --- 集計 ---
res = {s: [] for s in STRATS}   # strat -> list of (wide_ret, win_ret)
hits_detail = []
for rid, info in sorted(races.items(), key=lambda x: x[1]["date"]):
    try:
        fin, fav, wide, tan = parse_result(rid)
    except Exception as e:
        print(f"  [取得失敗] {rid} {e}")
        continue
    if fav is None:
        continue
    for uma in info["picks"]:
        if uma == fav:
            continue
        wret = wide.get(frozenset({uma, fav}), 0)
        win = tan.get(uma, 0) if fin.get(uma) == 1 else 0
        res[info["strat"]].append((wret, win))
        if wret > 0 or win > 0:
            hits_detail.append((info["date"], info["venue"], f"R{info['rno']}", info["strat"],
                                f"pick{uma}着{fin.get(uma,'?')}", f"1人気{fav}", f"ワイド{wret}", f"単勝{win}"))

JP = {"shiba": "芝", "dirt": "ダート", "shinba": "新馬"}
rc = {s: len([r for r in races.values() if r["strat"] == s]) for s in STRATS}
print(f"対象レース(state~2026-07-05): 芝{rc['shiba']} / ダ{rc['dirt']} / 新{rc['shinba']}\n")

print("=== 的中した買い目のみ抜粋 ===")
for d in hits_detail:
    print("  " + " ".join(str(x) for x in d))

print("\n=== 2026フォワード ROI (※稼働3-5週目・n極小の参考値) ===")
print(f"{'戦略':<6}{'n':>5}{'ワイド的中':>11}{'ワイドROI':>11}{'単勝的中':>10}{'単勝ROI':>10}")
allw = []
for st in STRATS:
    bets = res[st]; allw += bets
    if not bets:
        continue
    n = len(bets)
    wr = sum(b[0] for b in bets)/(n*100)*100; sr = sum(b[1] for b in bets)/(n*100)*100
    wh = sum(1 for b in bets if b[0] > 0); sh = sum(1 for b in bets if b[1] > 0)
    print(f"{JP[st]:<6}{n:>5}{wh:>6}({wh/n*100:.0f}%){wr:>9.1f}%{sh:>6}({sh/n*100:.0f}%){sr:>8.1f}%")
n = len(allw)
wr = sum(b[0] for b in allw)/(n*100)*100; sr = sum(b[1] for b in allw)/(n*100)*100
wh = sum(1 for b in allw if b[0] > 0); sh = sum(1 for b in allw if b[1] > 0)
print(f"{'合算':<6}{n:>5}{wh:>6}({wh/n*100:.0f}%){wr:>9.1f}%{sh:>6}({sh/n*100:.0f}%){sr:>8.1f}%")
