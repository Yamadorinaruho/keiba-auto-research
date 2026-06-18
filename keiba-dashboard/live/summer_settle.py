#!/usr/bin/env python3
"""【夜】本日の夏戦略の収支をSlack通知。1点1000円・単勝。
朝/巡回が保存した state/summer_sched_YYYYMMDD.json の pick を、
レース結果ページの着順・単勝配当と照合して損益を集計。

使い方: python3 -m live.summer_settle [YYYYMMDD]
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import fetch
from live import notify
from bs4 import BeautifulSoup

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
BET_PER = 1000


def result(race_id):
    """結果ページ → (馬番別着順dict, 単勝配当, 馬番別確定単勝オッズdict)"""
    html = fetch(f"https://race.netkeiba.com/race/result.html?race_id={race_id}",
                 cache_key=f"result_{race_id}.html", force=True)
    soup = BeautifulSoup(html, "html.parser")
    fin = {}
    odds = {}
    for tr in soup.select(".RaceTable01 tr, .ResultTable01 tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 4:
            continue
        try:
            rank, um = int(cells[0]), int(cells[2])
        except ValueError:
            continue
        fin[um] = rank
        # 確定単勝オッズ: 着差より後の最初の「N.N」形式セル(後3Fより前)を採用
        for cval in cells[8:]:
            m = re.match(r"^(\d{1,4}\.\d)$", cval)
            if m and 1.0 <= float(m.group(1)) <= 9999:
                odds[um] = float(m.group(1))
                break
    pay = None
    for tr in soup.select(".Payout_Detail_Table tr"):
        ths = tr.find_all("th")
        if ths and ths[0].get_text(strip=True) == "単勝":
            p = re.findall(r"\d+", tr.select_one(".Payout").get_text().replace(",", ""))
            if p:
                pay = float(p[0])
    return fin, pay, odds


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    date_iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    path = os.path.join(STATE_DIR, f"summer_sched_{date}.json")
    if not os.path.exists(path):
        print(f"[skip] {path} なし")
        return
    sched = json.load(open(path))
    bets = [r for r in sched["races"] if r.get("picks")]
    if not bets:
        notify.send(f"💴 *夏戦略 収支 {date_iso[5:].replace('-','/')}*\n本日の対象買い目なし")
        return
    n = nhit = 0
    stake = ret = 0
    lines = [f"💴 *夏戦略 本日の収支 {date_iso[5:].replace('-','/')}* (単勝¥{BET_PER:,}/点)", ""]
    drift = []  # (venue, rno, 馬名, 通知時オッズ, 確定オッズ, 戦略別オッズ上限)
    for r in bets:
        fin, pay, fodds = result(r["race_id"])
        for pk in r["picks"]:
            um = pk["umaban"]
            rank = fin.get(um)
            n += 1
            stake += BET_PER
            if rank == 1 and pay:
                nhit += 1
                ret += int(pay / 100 * BET_PER)
                lines.append(f"○ {r['venue']}{r['rno']}R {pk['horse']} → 1着 単勝{pay:.0f}円 (+¥{int(pay/100*BET_PER)-BET_PER:,})")
            else:
                lines.append(f"× {r['venue']}{r['rno']}R {pk['horse']} → {rank}着")
            # オッズ変動記録(較正用): 確定オッズは結果ページ優先、勝ち馬は配当からも補完
            of = fodds.get(um) or (pay / 100 if (rank == 1 and pay) else None)
            hi = 50 if r.get("strat") == "dirt" else 80   # 戦略別オッズ上限(芝10-80/ダ10-50)
            drift.append((r['venue'], r['rno'], pk['horse'], pk.get('odds_pre'), of, hi))
    net = ret - stake
    roi = ret / stake * 100 if stake else 0
    lines += ["", f"*的中 {nhit}/{n}  投資 ¥{stake:,} 払戻 ¥{ret:,}  収支 {'+' if net>=0 else ''}¥{net:,} (ROI {roi:.0f}%)*"]
    # オッズ変動レポート(通知時=発走15分前以内 → 確定)
    dl = ["", "📊 *オッズ変動 (通知時→確定)*"]
    deltas = []
    for v, rno, horse, op, of, hi in drift:
        if op and of:
            pct = (of - op) / op * 100
            deltas.append(pct)
            mark = "⚠️" if (of < 10 or of >= hi) else ""  # 確定でオッズ帯(芝10-80/ダ10-50)を外れた
            dl.append(f"・{v}{rno}R {horse}: {op:.1f}→{of:.1f}倍 ({pct:+.0f}%){mark}")
        else:
            ops = f"{op:.1f}" if op else "?"
            dl.append(f"・{v}{rno}R {horse}: {ops}→確定不明")
    if deltas:
        avg = sum(deltas) / len(deltas)
        out = sum(1 for _, _, _, op, of, hi in drift if op and of and (of < 10 or of >= hi))
        dl.append(f"_平均変動 {avg:+.0f}% / 確定でオッズ帯外(芝10-80/ダ10-50) {out}/{len(deltas)}頭_")
    lines += dl
    text = "\n".join(lines)
    print(text)
    notify.send(text)


if __name__ == "__main__":
    main()
