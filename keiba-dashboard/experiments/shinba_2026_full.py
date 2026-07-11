"""2026 新馬戦略(芝2歳新馬×父エピファネイア/エフフォーリア)の完全フォワード。
stateの欠落(6/1-6/16)を埋めるため、全開催日の芝2歳新馬を race_list→shutuba→horse(父)→result で直接取得。
単勝フラットROIを算出。実行: cd keiba-dashboard && python3 experiments/shinba_2026_full.py
"""
import sys, re
sys.path.insert(0, ".")
from live.netkeiba_scraper import fetch, parse_shutuba, parse_horse, parse_result
from bs4 import BeautifulSoup

SIRES = {"エピファネイア", "エフフォーリア"}
DATES = ["20260606","20260607","20260613","20260614","20260620","20260621",
         "20260627","20260628","20260704","20260705"]

def shinba_races(date):
    html = fetch(f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={date}",
                 cache_key=f"racelist_{date}.html", force=False)
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select('a[href*="race_id="]'):
        m = re.search(r"race_id=(\d{12})", a.get("href", ""))
        txt = a.get_text(" ", strip=True)
        if m and "2歳新馬" in txt and "芝" in txt:
            out.append((m.group(1), txt))
    return out

def horse_id_of(url):
    m = re.search(r"/horse/(\d+)", url or "")
    return m.group(1) if m else None

bets = []   # (date, race_id, umaban, horse, sire, finish, tansho)
for date in DATES:
    for rid, txt in shinba_races(date):
        try:
            sh = parse_shutuba(rid)
        except Exception:
            continue
        if sh.get("surface") != "芝":
            continue
        # 結果(着順・単勝配当)
        try:
            res = parse_result(rid)
        except Exception:
            res = None
        fin = {};
        # parse_result の horses から着順・馬番
        soup = BeautifulSoup(fetch(f"https://race.netkeiba.com/race/result.html?race_id={rid}",
                                   cache_key=f"result_{rid}.html"), "html.parser")
        tan = {}
        for tr in soup.select("tr"):
            c = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if len(c) >= 3 and c[0].isdigit() and c[2].isdigit():
                fin[int(c[2])] = int(c[0])
            th = tr.find("th")
            if th and th.get_text(strip=True) == "単勝":
                tds = tr.find_all("td")
                nums = re.findall(r"\d+", tds[0].get_text(" ", strip=True))
                pays = re.findall(r"([\d,]+)円", tds[1].get_text(" ", strip=True))
                if nums and pays:
                    tan[int(nums[0])] = int(pays[0].replace(",", ""))
        for h in sh["horses"]:
            hid = horse_id_of(h.get("馬URL"))
            if not hid or not h.get("馬番"):
                continue
            try:
                sire = parse_horse(hid).get("sire")
            except Exception:
                sire = None
            if sire in SIRES:
                u = h["馬番"]; f = fin.get(u)
                bets.append((date[:4]+"-"+date[4:6]+"-"+date[6:], rid, u, h.get("馬名"), sire, f,
                             tan.get(u, 0) if f == 1 else 0))

n = len(bets)
ret = sum(b[6] for b in bets)
wins = [b for b in bets if b[5] == 1]
print(f"=== 2026 新馬戦略 完全フォワード (芝2歳新馬×エピ/エフ, 6/6-7/5) ===")
print(f"ピック {n}頭 / 勝ち {len(wins)}頭 (的中率{len(wins)/n*100:.1f}%) / 単勝ROI {ret/(n*100)*100:.1f}%")
by_sire = {}
for b in bets:
    d = by_sire.setdefault(b[4], [0, 0, 0.0])
    d[0] += 1; d[1] += 1 if b[5] == 1 else 0; d[2] += b[6]
for s, (nn, w, r) in by_sire.items():
    print(f"  父{s}: {nn}頭 {w}勝 単勝ROI {r/(nn*100)*100:.0f}%")
print("\n勝ち馬:")
for b in sorted(wins):
    print(f"  {b[0]} {b[3]}(父{b[4]}) 単勝{b[6]}円")
# 月旬別に欠落補完の確認
print("\n日別ピック数:")
from collections import Counter
for d, c in sorted(Counter(b[0] for b in bets).items()):
    print(f"  {d}: {c}頭")
