#!/usr/bin/env python3
"""【オッズ記録】JRA全レース・全馬の発走10〜1分前オッズを時系列記録(追記TSV)。
戦略の通知(summer_notify/dirt/shinba)とは独立した較正用ロガー。
  巡回(毎分)から run() を呼び、各レースが発走10〜1分前の間、1分ごとに全出走馬の
  単勝オッズ・人気をスナップショットして state/odds_log_YYYYMMDD.tsv に1行=1馬で追記。
  夜に summer_settle から finalize() を呼び、結果ページの確定単勝オッズを lead=final 行で追記。
TSV(追記only)にすることで、毎巡回まるごと書き直すJSON方式で起きていたgitマージ破損を構造的に回避。
  1行 = race_id, surface, distance, post, lead(分前 or final), captured_at, official_datetime, 馬番, オッズ, 人気
state は workflow がコミットして巡回間で共有(state/odds_log_*.tsv / odds_races_*.json)。

使い方: python3 -m live.odds_log [YYYYMMDD]
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import fetch, parse_shutuba, get_race_ids_for_date, live_odds
from bs4 import BeautifulSoup

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
SNAP_LO, SNAP_HI = 1, 10   # 発走10分前〜1分前を1分ごとに時系列スナップショット
COLS = ["race_id", "surface", "distance", "post", "lead", "captured_at",
        "official_datetime", "umaban", "odds", "pop"]


def _races_path(date):
    return os.path.join(STATE_DIR, f"odds_races_{date}.json")


def _log_path(date):
    return os.path.join(STATE_DIR, f"odds_log_{date}.tsv")


def _post_time(rid):
    html = fetch(f"https://race.netkeiba.com/race/shutuba.html?race_id={rid}",
                 cache_key=f"shutuba_{rid}.html")
    soup = BeautifulSoup(html, "html.parser")
    rd = soup.select_one(".RaceData01")
    if rd:
        m = re.search(r"(\d{1,2}):(\d{2})発走", rd.get_text(" ", strip=True))
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def _race_list(date):
    """{race_id: 'HH:MM'} を返す。無ければ全レースの発走時刻を取得して保存(1日1回)。"""
    p = _races_path(date)
    if os.path.exists(p):
        return json.load(open(p))
    races = {}
    for rid in get_race_ids_for_date(date):
        try:
            races[rid] = _post_time(rid)
        except Exception:
            races[rid] = None
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(races, open(p, "w"), ensure_ascii=False, indent=1)
    return races


def _recorded_keys(date):
    """記録済みの (race_id, lead) 集合。重複記録防止に使う。"""
    p = _log_path(date)
    keys = set()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            next(f, None)   # ヘッダ
            for line in f:
                c = line.rstrip("\n").split("\t")
                if len(c) >= 5:
                    keys.add((c[0], c[4]))   # (race_id, lead)
    return keys


def _append_rows(date, rows):
    """TSVに追記(ファイル無ければヘッダ付き新規)。"""
    p = _log_path(date)
    os.makedirs(STATE_DIR, exist_ok=True)
    new = not os.path.exists(p)
    with open(p, "a", encoding="utf-8") as f:
        if new:
            f.write("\t".join(COLS) + "\n")
        for r in rows:
            f.write("\t".join("" if x is None else str(x) for x in r) + "\n")


def _snapshot(rid):
    """最新単勝オッズをAJAXで取得(リロード相当) → (official_datetime, surface, distance, {馬番:{odds,pop}})。"""
    dt, od = live_odds(rid)
    horses = {str(um): {"odds": v["odds"], "pop": v["pop"]} for um, v in od.items()}
    s = parse_shutuba(rid)   # surface/distance用(キャッシュ可)
    return dt, s.get("surface"), s.get("distance"), horses


def run(date=None, now=None):
    """巡回(1分ごと想定)から呼ぶ。発走10〜1分前のレースを全頭スナップショットしTSVに追記。
    同じレース・同じ分前は二重記録しない(=各分1スナップ)。"""
    now = now or datetime.datetime.now()
    date = date or now.strftime("%Y%m%d")
    races = _race_list(date)
    recorded = _recorded_keys(date)
    rows = []
    for rid, post in races.items():
        if not post:
            continue
        hh, mm = map(int, post.split(":"))
        post_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        lead = (post_dt - now).total_seconds() / 60.0
        if not (SNAP_LO <= lead <= SNAP_HI):
            continue
        leadm = round(lead)
        if (rid, str(leadm)) in recorded:
            continue   # この分前は記録済み(同一分の重複巡回を弾く)
        try:
            dt, surface, distance, horses = _snapshot(rid)
        except Exception as e:
            print(f"[odds_log err] {rid}: {e}")
            continue
        if not horses:   # オッズ未確定(発売前等)はまだ記録しない=次の巡回で再挑戦
            continue
        cap = now.strftime("%Y-%m-%d %H:%M")
        for um, v in horses.items():
            rows.append([rid, surface, distance, post, leadm, cap, dt, um, v["odds"], v["pop"]])
        recorded.add((rid, str(leadm)))
    if rows:
        _append_rows(date, rows)
        print(f"[odds_log] {len(rows)}行 追記")
    return len(rows)


def _final_odds(rid):
    """結果ページから確定単勝オッズ {馬番: odds}。"""
    html = fetch(f"https://race.netkeiba.com/race/result.html?race_id={rid}",
                 cache_key=f"result_{rid}.html")
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for tr in soup.select(".RaceTable01 tr, .ResultTable01 tr"):
        c = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(c) < 11:
            continue
        try:
            um = int(c[2])
        except ValueError:
            continue
        m = re.match(r"^(\d{1,4}\.\d)$", c[10])
        if m:
            out[str(um)] = float(m.group(1))
    return out


def finalize(date=None):
    """夜に呼ぶ。記録済み全レースへ確定単勝オッズを lead=final 行で追記(締切前 vs 確定の比較用)。"""
    date = date or datetime.date.today().strftime("%Y%m%d")
    if not os.path.exists(_log_path(date)):
        return 0
    recorded = _recorded_keys(date)
    race_ids = {k[0] for k in recorded}
    already_final = {k[0] for k in recorded if k[1] == "final"}
    rows = []
    done = 0
    for rid in sorted(race_ids):
        if rid in already_final:
            continue
        try:
            fo = _final_odds(rid)
        except Exception as e:
            print(f"[odds_log final err] {rid}: {e}")
            continue
        if fo:
            for um, od in fo.items():
                rows.append([rid, "", "", "", "final", "", "", um, od, ""])
            done += 1
    if rows:
        _append_rows(date, rows)
        print(f"[odds_log] {done}R に確定オッズ付与")
    return done


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    run(date)   # 毎分cronから呼ばれ、10〜1分前のレースを時系列スナップショット
