#!/usr/bin/env python3
"""【巡回】発走30分前のレースの本命をSlack通知。
朝に summer_schedule が保存した発走時刻リストを見て、現在時刻が
発走30分前(±8分窓)のレースだけ、馬体重・確定オッズ込みの4軸スコアで本命算出→通知。
通知済みフラグをstateに書き戻して二重通知を防ぐ。

スコア(decision 155): 外枠(5-8) + 前走4角中団以降(>0.33) + 前走6着以下 + 馬体重450-470
母集団: 3歳牝 芝 未勝利 4-10番人気 単勝150倍以下
使い方: python3 -m live.summer_notify [YYYYMMDD]  (env TZ=Asia/Tokyo 前提)
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import parse_shutuba, fetch
from live import notify
from bs4 import BeautifulSoup

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
LEAD_MIN = 30      # 発走何分前に通知するか
WINDOW = 8         # 巡回間隔(15分)を取りこぼさない窓 ±8分
BET_PER = 1000
MIN_SCORE = 3      # この点以上の該当馬を全部買う (decision 156)


def get_weight(race_id):
    html = fetch(f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}",
                 cache_key=f"shutuba_{race_id}.html", force=True)  # 直前情報のため再取得
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for tr in soup.select(".Shutuba_Table tr.HorseList"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        num = tds[1].get_text(strip=True)
        m = re.search(r"(\d{3})\(", tr.get_text(" ", strip=True))
        if num.isdigit() and m:
            out[int(num)] = int(m.group(1))
    return out


def prev_run(horse_id, before_date):
    html = fetch(f"https://db.netkeiba.com/horse/result/{horse_id}/",
                 cache_key=f"hresult_{horse_id}.html")
    soup = BeautifulSoup(html, "html.parser")
    t = soup.select_one(".db_h_race_results")
    if not t:
        return None, None
    idx = {h.get_text(strip=True): i for i, h in enumerate(t.select("thead th"))}
    for tr in t.select("tbody tr"):
        tds = [td.get_text(strip=True) for td in tr.select("td")]
        if len(tds) < 10:
            continue
        d = tds[idx.get("日付", 0)].replace("/", "-")
        if not (d and d < before_date):
            continue
        fin = int(tds[idx["着順"]]) if "着順" in idx and tds[idx["着順"]].isdigit() else None
        nrun = int(tds[idx["頭数"]]) if "頭数" in idx and tds[idx["頭数"]].isdigit() else None
        c4 = None
        if "通過" in idx and "-" in tds[idx["通過"]]:
            try:
                c4 = int(tds[idx["通過"]].split("-")[-1])
            except ValueError:
                pass
        return ((c4 / nrun) if (c4 and nrun) else None), fin
    return None, None


def axes(c):
    rel = c["rel"]
    return [("外枠5-8", c["枠"] >= 5, f"{c['枠']}枠"),
            ("前走中団以降", rel is not None and rel > 0.33, f"4角{rel:.0%}" if rel is not None else "前走不明"),
            ("前走6着以下", c["前着"] is not None and c["前着"] >= 6, f"前走{c['前着']}着" if c["前着"] is not None else "前走不明"),
            ("中型450-470", c["体重"] is not None and 450 <= c["体重"] <= 470, f"{c['体重']}kg" if c["体重"] is not None else "体重不明")]


def reason(c):
    lbl = {"外枠5-8": "外枠", "前走中団以降": "前走で脚を余す", "前走6着以下": "前走負けて人気落ち", "中型450-470": "好適馬体重"}
    ok = [lbl[n] for n, hit, _ in axes(c) if hit]
    return " / ".join(ok) if ok else "母集団該当のみ"


def build_pick(race_id, date_iso):
    s = parse_shutuba(race_id)
    if s["surface"] != "芝" or s["class"] != "未勝利":
        return None
    wmap = get_weight(race_id)
    cands = []
    for h in s["horses"]:
        sa = h.get("性齢", "")
        if not (sa.startswith("牝") and sa.endswith("3")):
            continue
        pop, odds = h.get("人気"), h.get("単勝オッズ")
        if pop is None or odds is None or not (4 <= pop <= 10) or odds >= 150:
            continue
        waku = int(h["枠"]) if h.get("枠", "").isdigit() else 0
        wt = wmap.get(h["馬番"])
        rel, fin = prev_run(h["馬ID"], date_iso) if h.get("馬ID") else (None, None)
        sc = (int(waku >= 5) + int(rel is not None and rel > 0.33)
              + int(fin is not None and fin >= 6) + int(wt is not None and 450 <= wt <= 470))
        cands.append({"馬番": h["馬番"], "馬名": h["馬名"], "人気": pop, "odds": odds,
                      "枠": waku, "rel": rel, "前着": fin, "体重": wt, "score": sc})
    if not cands:
        return None
    cands.sort(key=lambda x: (-x["score"], -x["odds"]))
    return {"race_name": s["race_name"], "distance": s["distance"],
            "honmei": cands[0], "others": cands[1:]}


def now_jst():
    return datetime.datetime.now()  # workflowで TZ=Asia/Tokyo を設定


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else now_jst().strftime("%Y%m%d")
    date_iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    path = os.path.join(STATE_DIR, f"summer_sched_{date}.json")
    if not os.path.exists(path):
        print(f"[skip] スケジュール未生成: {path}")
        return
    sched = json.load(open(path))
    now = now_jst()
    changed = False
    for r in sched["races"]:
        if r.get("notified"):
            continue
        hh, mm = map(int, r["post"].split(":"))
        post_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        lead = (post_dt - now).total_seconds() / 60.0  # 発走まで何分
        if abs(lead - LEAD_MIN) > WINDOW:
            continue
        try:
            p = build_pick(r["race_id"], date_iso)
        except Exception as e:
            print(f"[err] {r['race_id']}: {e}")
            continue
        if not p:
            r["notified"] = True
            changed = True
            continue
        # score>=3 の該当馬を全部買う (cc-memory decision 156)
        allc = [p["honmei"]] + p["others"]
        buys = [c for c in allc if c["score"] >= MIN_SCORE]
        head = f"🏇 *発走{int(round(lead))}分前* {r['venue']}{r['rno']}R {p['race_name']} ({p['distance']}m)"
        if not buys:
            # 買い目なし(score<3のみ)。通知は出さず記録のみ
            r["notified"] = True
            r["picks"] = []
            changed = True
            print(f"{head}  → 買い目なし(最高score {allc[0]['score']})")
            continue
        lines = [head, f"◎買い目 {len(buys)}点 (score≥{MIN_SCORE}・各単勝¥{BET_PER:,})"]
        for c in buys:
            ax = " ".join(f"{'✅' if hit else '⬜'}{n}({v})" for n, hit, v in axes(c))
            lines.append(f"・{c['馬番']}番 *{c['馬名']}* {c['人気']}人気 *{c['odds']}倍* (score {c['score']}/4)")
            lines.append(f"   {ax} → {reason(c)}")
        skipped = [c for c in allc if c["score"] < MIN_SCORE]
        if skipped:
            lines.append("_見送り(score<3): " + " , ".join(f"{c['馬番']}{c['馬名']}(s{c['score']})" for c in skipped) + "_")
        text = "\n".join(lines)
        print(text)
        notify.send(text)
        r["notified"] = True
        r["picks"] = [{"umaban": c["馬番"], "horse": c["馬名"], "odds_pre": c["odds"], "score": c["score"]} for c in buys]
        changed = True
    if changed:
        json.dump(sched, open(path, "w"), ensure_ascii=False, indent=1)
        print("[state] updated")


if __name__ == "__main__":
    main()
