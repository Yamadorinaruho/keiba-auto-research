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


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    date_iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    ids = get_race_ids_for_date(date)
    target = [r for r in ids if VENUE.get(r[4:6]) in LOCAL4]
    races = []
    for rid in target:
        try:
            s = parse_shutuba(rid)
        except Exception:
            continue
        if s["surface"] != "芝" or s["class"] != "未勝利":
            continue
        # 3歳牝が1頭でもいるレースのみ対象
        if not any(h.get("性齢", "").startswith("牝") and h.get("性齢", "").endswith("3") for h in s["horses"]):
            continue
        pt = post_time(rid)
        if not pt:
            continue
        races.append({"race_id": rid, "venue": VENUE.get(rid[4:6]),
                      "rno": int(rid[-2:]), "post": pt, "notified": False})
    races.sort(key=lambda x: x["post"])
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"summer_sched_{date}.json")
    with open(path, "w") as f:
        json.dump({"date": date_iso, "races": races}, f, ensure_ascii=False, indent=1)
    msg = f"📅 *夏戦略 本日の対象レース {date_iso[5:].replace('-','/')}* ({len(races)}R)\n"
    msg += "\n".join(f"  {r['post']} {r['venue']}{r['rno']}R (発走30分前に本命通知)" for r in races) or "  対象レースなし"
    print(msg)
    notify.send(msg)
    print(f"[state] {path}")


if __name__ == "__main__":
    main()
