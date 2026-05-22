"""指定日(複数日可)の G3+ｵｰﾌﾟﾝレース全部を netkeiba から並列取込
Usage:
  python live/scrape_all.py 20260509              # 単日
  python live/scrape_all.py 20260509 20260510     # 複数日
  python live/scrape_all.py weekend               # 直近土日
"""
import sys, sqlite3, time
from pathlib import Path
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from live.netkeiba_scraper import get_race_ids_for_date, parse_shutuba
from live.scrape_to_db import ingest_race, fit_pci3_v5_beta
from plot_wealth_data import DB

TARGET_CLASSES = ["Ｇ３", "Ｇ１", "Ｇ２", "ｵｰﾌﾟﾝ", "オープン", "G1", "G2", "G3"]


def is_target_race(shutuba):
    """G3 or オープン特別 平地 (= 障害・リステッド・平場除外) を判定"""
    cls = shutuba.get("class", "")
    name = shutuba.get("race_name", "")
    surface = shutuba.get("surface", "")
    text = cls + " " + name + " " + surface
    # 除外: 障害レース, 平場, リステッド, G1/G2 (戦略対象外)
    for excl in ["障害", "障", "ｼﾞｬﾝﾌﾟ", "ジャンプ",
                  "未勝利", "新馬", "1勝", "2勝", "3勝",
                  "(L)", "OP(L)", "リステッド",
                  "Ｇ１", "Ｇ２", "GⅠ", "GⅡ", "G1", "G2"]:
        if excl in text:
            return False
    # 採用: G3 または オープン (平地)
    for kw in ["Ｇ３", "GⅢ", "G3", "オープン", "ｵｰﾌﾟﾝ", "OP"]:
        if kw in text:
            return True
    return False


def get_dates_for_weekend():
    """直近の土日"""
    today = date.today()
    # 直近の土曜
    days_until_sat = (5 - today.weekday()) % 7
    sat = today + timedelta(days=days_until_sat)
    sun = sat + timedelta(days=1)
    return [sat.strftime("%Y%m%d"), sun.strftime("%Y%m%d")]


def run_scrape(dates, notify_slack: bool = True):
    """指定日 (YYYYMMDD のリスト) を取込み、サマリ dict を返す。

    Args:
        dates: ["20260523", ...]
        notify_slack: True なら Slack 通知も飛ばす

    Returns:
        {"dates": [...], "total_races": int, "total_horses": int,
         "elapsed_sec": float, "errors": [{"race_id": ..., "error": ...}, ...]}
    """
    from live import notify
    print(f"対象日: {dates}", flush=True)
    if notify_slack:
        notify.send(f"🐎 netkeiba スクレイピング開始 ({', '.join(dates)})")
    t_start = time.time()
    print("v5 PCI3 係数 fit中...", flush=True)
    beta = fit_pci3_v5_beta()

    total_races = total_horses = 0
    errors = []
    for d in dates:
        print(f"\n=== {d} ===", flush=True)
        rids = get_race_ids_for_date(d)
        print(f"  全レース: {len(rids)}", flush=True)

        # shutuba を 並列取得してフィルタ
        def fetch_shutuba(rid):
            try:
                return rid, parse_shutuba(rid)
            except Exception as e:
                return rid, {"error": str(e)}
        target_rids = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            for rid, s in ex.map(fetch_shutuba, rids):
                if "error" in s:
                    print(f"  ⚠️ {rid} shutuba失敗: {s['error']}")
                    errors.append({"race_id": rid, "error": s["error"]})
                    continue
                if is_target_race(s):
                    target_rids.append((rid, s.get("race_name", "")))
        print(f"  G3+OP対象 (障害除外): {len(target_rids)}件")

        # 各対象レースを並列取込 (DBはレースごとに開閉)
        def ingest_one(args):
            rid, name = args
            try:
                conn = sqlite3.connect(DB, timeout=30)
                n = ingest_race(rid, beta, conn)
                conn.close()
                return rid, name, n, None
            except Exception as e:
                return rid, name, 0, str(e)

        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(ingest_one, t): t for t in target_rids}
            for fut in as_completed(futures):
                rid, name, n, err = fut.result()
                if err:
                    print(f"  ⚠️ {rid} {name} 失敗: {err}")
                    errors.append({"race_id": rid, "error": err})
                else:
                    print(f"  ✓ {rid} {name}: {n}頭")
                    total_races += 1
                    total_horses += n

    elapsed = time.time() - t_start
    print(f"\n✅ 全完了: {total_races}レース / {total_horses}頭 ({elapsed:.0f}秒)")
    if notify_slack:
        notify.send(f"✓ scrape完了: {total_races}レース / {total_horses}頭 ({elapsed:.0f}秒)")
    return {
        "dates": dates,
        "total_races": total_races,
        "total_horses": total_horses,
        "elapsed_sec": round(elapsed, 1),
        "errors": errors,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    if sys.argv[1] == "weekend":
        dates = get_dates_for_weekend()
    else:
        dates = sys.argv[1:]
    run_scrape(dates)


if __name__ == "__main__":
    main()
