#!/usr/bin/env python3
"""【朝】夏戦略・対象レースの発走時刻リストを確定して保存。
5場(小倉/福島/函館/新潟/札幌)の 3歳牝・芝・未勝利 を抽出し、発走時刻を記録。
巡回ジョブ(summer_notify)がこのリストを見て30分前に本命を通知する。

使い方: python3 -m live.summer_schedule [YYYYMMDD]
出力: state/summer_sched_YYYYMMDD.json
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import get_race_ids_for_date, parse_shutuba, fetch
from live.summer_notify import prev_run, lin_bonus   # 前走rel/着順/父/キャリア・血統加点を共用
from live import summer_dirt                          # ダート第2戦略の対象判定
from live import summer_shinba                        # 新馬第3戦略(エピ系)の対象判定
from live.sire_lineage_map import LINEAGE
from live import notify
from bs4 import BeautifulSoup

# 純ローカル4場+札幌(=北海道の少頭数ローカル色、函館と同性質。decision 163)
# 中京は除外: 波乱が11番人気以下の超大穴に逃げ4-10番人気が取れない(99%)
LOCAL4 = {"小倉", "福島", "函館", "新潟", "札幌"}
VENUE = {"01":"札幌","02":"函館","03":"福島","04":"新潟","05":"東京",
         "06":"中山","07":"中京","08":"京都","09":"阪神","10":"小倉"}
STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")


def post_time(race_id):
    html = fetch(f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}",
                 cache_key=f"shutuba_{race_id}.html")
    soup = BeautifulSoup(html, "html.parser")
    rd = soup.select_one(".RaceData01")
    if rd:
        m = re.search(r"(\d{1,2}):(\d{2})発走", rd.get_text(" ", strip=True))
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def is_3hinba(h):
    sa = h.get("性齢", "")
    return sa.startswith("牝") and sa.endswith("3")


def cands_for(s, hfilter, date_iso):
    """対象馬の不変特徴(前走rel/着順/父系統/キャリア数)を朝に1回だけ計算。"""
    out = []
    for h in s["horses"]:
        if not hfilter(h) or not h.get("馬ID"):
            continue
        try:
            rel, fin, sire, n_prev = prev_run(h["馬ID"], date_iso)
        except Exception:
            rel = fin = sire = None; n_prev = 0
        out.append({"umaban": h["馬番"], "horse": h["馬名"], "rel": rel, "fin": fin,
                    "lin": LINEAGE.get(sire) if sire else None, "n_prev": n_prev})
    return out


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    date_iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    races = []
    for rid in get_race_ids_for_date(date):   # 全会場走査(ダートは全場対象)
        venue = VENUE.get(rid[4:6])
        try:
            s = parse_shutuba(rid)
        except Exception:
            continue
        # 芝戦略: 5場 芝 未勝利 3歳牝 / ダート第2戦略: 全場 ダ≤1400 未勝利〜OP 牝 / 新馬第3戦略: 全場 芝 2歳新馬 エピ系
        if venue in LOCAL4 and s["surface"] == "芝" and s["class"] == "未勝利" \
                and any(is_3hinba(h) for h in s["horses"]):
            strat, cands = "shiba", cands_for(s, is_3hinba, date_iso)
        elif summer_dirt.is_target_race(s):
            strat, cands = "dirt", cands_for(s, summer_dirt.target_horse, date_iso)
        elif summer_shinba.is_target_race(s):
            cands = summer_shinba.cands_for(s, date_iso)   # エピ系産駒のみ抽出
            if not cands:   # エピ系不在ならスキップ
                continue
            strat = "shinba"
        else:
            continue
        pt = post_time(rid)
        if not pt:
            continue
        races.append({"race_id": rid, "venue": venue, "rno": int(rid[-2:]), "post": pt,
                      "notified": False, "strat": strat, "cands": cands})
    races.sort(key=lambda x: x["post"])
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"summer_sched_{date}.json")
    with open(path, "w") as f:
        json.dump({"date": date_iso, "races": races}, f, ensure_ascii=False, indent=1)
    def struct_shiba(c):  # 芝: 前走中団以降+前走6着下+血統
        return (int(c["rel"] is not None and c["rel"] > 0.33)
                + int(c["fin"] is not None and c["fin"] >= 6) + lin_bonus(c["lin"]))
    def struct_dirt(c):   # ダート: 前付け+米国系+前走9着以内(馬体重は当日)
        return (int(c["rel"] is not None and c["rel"] <= 0.33)
                + int(c["lin"] == "米国系") + int(c["fin"] is not None and c["fin"] <= 9))
    ns = sum(r["strat"] == "shiba" for r in races); nd = sum(r["strat"] == "dirt" for r in races)
    nb = sum(r["strat"] == "shinba" for r in races)
    lines = [f"📅 *夏戦略 対象レース {date_iso[5:].replace('-','/')}* (芝{ns}R / ダ{nd}R / 新馬{nb}R)",
             "_朝の事前計算: 構造スコア(馬体重・オッズ・人気は当日加算→発走15分前以内に最終通知)_"]
    for r in races:
        if r["strat"] == "shinba":   # 新馬エピ系: 構造スコアなし。対象産駒を列挙
            lines.append(f"\n*{r['post']} [新馬]{r['venue']}{r['rno']}R* (エピ系全頭買い)")
            for c in r["cands"]:
                lines.append(f"  {c['umaban']}番 {c['horse']} (父{c['sire']})")
            continue
        st = struct_shiba if r["strat"] == "shiba" else struct_dirt
        tag = "芝" if r["strat"] == "shiba" else "ダ"
        lines.append(f"\n*{r['post']} [{tag}]{r['venue']}{r['rno']}R*")
        for c in sorted([c for c in r["cands"] if c["n_prev"] >= 2], key=lambda c: -st(c)):
            relstr = f"4角{c['rel']:.0%}" if c["rel"] is not None else "前走不明"
            finstr = f"前走{c['fin']}着" if c["fin"] is not None else "前走?"
            lines.append(f"  [構造{st(c)}] {c['umaban']}番 {c['horse']} ({finstr}/{relstr}/{c['lin'] or '血統-'})")
    msg = "\n".join(lines) if races else f"📅 *夏戦略 対象レース {date_iso[5:].replace('-','/')}*\n  対象レースなし"
    print(msg)
    notify.send(msg)
    print(f"[state] {path}")


if __name__ == "__main__":
    main()
