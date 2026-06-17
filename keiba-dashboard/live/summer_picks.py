#!/usr/bin/env python3
"""夏未勝利牝・本命1点ロジック 買い目生成 + Slack通知

戦略 (cc-memory decision 153/154/155):
  母集団: 6/16-8月末 × 3歳牝 × 芝 × 未勝利 × {小倉/福島/函館/新潟}
          × 4-10番人気 × 単勝150倍以下
  スコア = 外枠(5-8枠) + 前走4角中団以降(前4角/前走頭数>0.33)
         + 前走6着以下 + 馬体重450-470kg
  各レース最高スコア1頭(同点は高オッズ)を本命として単勝。
  賭け方: 本命4 : 他該当馬1 の傾斜配分(任意)。

WF実績: 本命1点 ROI 147.4% / 本命4:他1 ROI 129.4%(8年全プラス)。
※多重性込みで実期待値は割引いて見る。2026夏が前向きテスト。

使い方:
  python3 summer_picks.py            # 今日
  python3 summer_picks.py 20260621   # 日付指定
  SLACK_WEBHOOK_URL=... で通知。未設定なら標準出力のみ。
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import get_race_ids_for_date, parse_shutuba, fetch
from live import notify
from bs4 import BeautifulSoup

LOCAL4 = {"小倉", "福島", "函館", "新潟"}
VENUE = {"01":"札幌","02":"函館","03":"福島","04":"新潟","05":"東京",
         "06":"中山","07":"中京","08":"京都","09":"阪神","10":"小倉"}
STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
BET_PER = 1000  # 1点1000円


def get_baba_weight(race_id):
    """出走表ページから 馬番→馬体重"""
    html = fetch(f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}",
                 cache_key=f"shutuba_{race_id}.html")
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
    """前走(before_dateより前の最新)の (4角/頭数=相対位置, 着順)"""
    html = fetch(f"https://db.netkeiba.com/horse/result/{horse_id}/",
                 cache_key=f"hresult_{horse_id}.html")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one(".db_h_race_results")
    if not table:
        return None, None
    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    idx = {h: i for i, h in enumerate(headers)}
    for tr in table.select("tbody tr"):
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
        rel = (c4 / nrun) if (c4 and nrun) else None
        return rel, fin
    return None, None


def pick_for_date(date):
    date_iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    mm, dd = int(date[4:6]), int(date[6:8])
    in_season = (mm == 6 and dd >= 16) or mm in (7, 8)
    ids = get_race_ids_for_date(date)
    target = [r for r in ids if VENUE.get(r[4:6]) in LOCAL4]
    picks = []
    for rid in target:
        venue = VENUE.get(rid[4:6])
        rno = int(rid[-2:])
        try:
            s = parse_shutuba(rid)
        except Exception:
            continue
        if s["surface"] != "芝" or s["class"] != "未勝利":
            continue
        wmap = get_baba_weight(rid)
        cands = []
        for h in s["horses"]:
            sa = h.get("性齢", "")
            if not (sa.startswith("牝") and sa.endswith("3")):
                continue
            pop, odds = h.get("人気"), h.get("単勝オッズ")
            if pop is None or odds is None:
                continue
            if not (4 <= pop <= 10) or odds >= 150:
                continue
            waku = int(h["枠"]) if h.get("枠", "").isdigit() else 0
            wt = wmap.get(h["馬番"])
            rel, fin = prev_run(h["馬ID"], date_iso) if h.get("馬ID") else (None, None)
            sc = ((1 if waku >= 5 else 0)
                  + (1 if (rel is not None and rel > 0.33) else 0)
                  + (1 if (fin is not None and fin >= 6) else 0)
                  + (1 if (wt is not None and 450 <= wt <= 470) else 0))
            cands.append({"馬番": h["馬番"], "馬名": h["馬名"], "人気": pop, "odds": odds,
                          "枠": waku, "rel": rel, "前着": fin, "体重": wt, "score": sc})
        if not cands:
            continue
        cands.sort(key=lambda x: (-x["score"], -x["odds"]))
        picks.append({"race_id": rid, "venue": venue, "rno": rno, "race_name": s["race_name"],
                      "distance": s["distance"], "honmei": cands[0], "others": cands[1:]})
    return date_iso, in_season, picks


def save_state(date, date_iso, picks):
    """夜の収支集計用に本命を保存"""
    os.makedirs(STATE_DIR, exist_ok=True)
    rec = {"date": date_iso, "bet_per": BET_PER,
           "picks": [{"race_id": p["race_id"], "venue": p["venue"], "rno": p["rno"],
                      "umaban": p["honmei"]["馬番"], "horse": p["honmei"]["馬名"],
                      "odds_am": p["honmei"]["odds"]} for p in picks]}
    path = os.path.join(STATE_DIR, f"summer_{date}.json")
    with open(path, "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return path


def _axes(c):
    """各馬のスコア4軸の充足を○×で。(外枠/前走中団以降/前走6着下/中型馬体重)"""
    rel = c["rel"]
    return [
        ("外枠5-8", c["枠"] >= 5, f"{c['枠']}枠"),
        ("前走中団以降", (rel is not None and rel > 0.33), (f"4角{rel:.0%}地点" if rel is not None else "前走不明")),
        ("前走6着以下", (c["前着"] is not None and c["前着"] >= 6), (f"前走{c['前着']}着" if c["前着"] is not None else "前走不明")),
        ("中型450-470", (c["体重"] is not None and 450 <= c["体重"] <= 470), (f"{c['体重']}kg" if c["体重"] is not None else "体重不明")),
    ]


def _reason(c):
    ok = [name for name, hit, _ in _axes(c) if hit]
    if not ok:
        return "妙味シグナルなし(母集団該当のみ)"
    label = {"外枠5-8": "外枠", "前走中団以降": "前走で脚を余す(能力未露呈)",
             "前走6着以下": "前走負けて人気落ち(巻き返し)", "中型450-470": "好適馬体重"}
    return " / ".join(label[n] for n in ok)


def to_slack(date_iso, in_season, picks):
    head = f"🏇 *夏未勝利牝・本命リスト {date_iso[5:].replace('-','/')}*"
    if not in_season:
        head += "\n_(夏季6/16-8月末 外の参考表示)_"
    if not picks:
        return head + "\n対象レース(4場×3歳牝芝未勝利)なし"
    lines = [head,
             "_戦略: 6/16-8月 純ローカル4場 3歳牝 芝 未勝利 4-10人気。"
             "スコア=外枠+前走中団以降+前走6着下+馬体重450-470 の最高点を本命_", ""]
    for p in picks:
        h = p["honmei"]
        lines.append(f"📍 *{p['venue']}{p['rno']}R* {p['race_name']} ({p['distance']}m)  該当{1+len(p['others'])}頭")
        # 本命
        ax = " ".join(f"{'✅' if hit else '⬜'}{name}({val})" for name, hit, val in _axes(h))
        lines.append(f"   ◎本命 {h['馬番']}番 *{h['馬名']}* {h['人気']}人気 *{h['odds']}倍* (score {h['score']}/4)")
        lines.append(f"      {ax}")
        lines.append(f"      → 狙い: {_reason(h)}")
        # 候補(他該当馬)を理由付きで
        for c in p["others"]:
            cax = "".join("✅" if hit else "⬜" for _, hit, _ in _axes(c))
            lines.append(f"   ・対抗 {c['馬番']}番 {c['馬名']} {c['人気']}人気 {c['odds']}倍 "
                         f"(score {c['score']}/4 {cax}) {_reason(c)}")
        lines.append("")
    lines.append(f"*本命 {len(picks)}頭* / 賭け方: 本命4 : 他該当馬1 (例 本命¥4000 + 他各¥1000)")
    lines.append("_◎=各レース最高スコア。同点は高オッズ優先。実期待値はWFの割引込みで見ること_")
    return "\n".join(lines)


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    date_iso, in_season, picks = pick_for_date(date)
    text = to_slack(date_iso, in_season, picks)
    print(text)
    notify.send(text)
    if picks:
        p = save_state(date, date_iso, picks)
        print(f"[state] saved {p}")


if __name__ == "__main__":
    main()
