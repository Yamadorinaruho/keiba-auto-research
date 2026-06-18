#!/usr/bin/env python3
"""【オッズ記録】JRA全レース・全馬の発走15分前オッズを記録し、夜に確定オッズを付与。
戦略の通知(summer_notify/dirt/shinba)とは独立した較正用ロガー。
  巡回(3分毎)から run() を呼び、各レースが発走1〜15分前に入った最初のタイミングで
  全出走馬の単勝オッズ・人気を1回スナップショット → state/odds_log_YYYYMMDD.json。
  夜に summer_settle から finalize() を呼び、記録済みレースの確定オッズを付与。
state は workflow がコミットして巡回間で共有(state/odds_*.json)。

使い方: python3 -m live.odds_log [YYYYMMDD]   (単体は当日の race list 生成のみ)
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import fetch, parse_shutuba, get_race_ids_for_date, live_odds
from bs4 import BeautifulSoup

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
SNAP_LO, SNAP_HI = 1, 15   # 発走この分前以内で未記録なら1回スナップショット


def _races_path(date):
    return os.path.join(STATE_DIR, f"odds_races_{date}.json")


def _log_path(date):
    return os.path.join(STATE_DIR, f"odds_log_{date}.json")


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


def _load_log(date):
    p = _log_path(date)
    return json.load(open(p)) if os.path.exists(p) else {}


def _save_log(date, log):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(log, open(_log_path(date), "w"), ensure_ascii=False, indent=1)


def _snapshot(rid):
    """最新単勝オッズをAJAXで取得(リロード相当) → (official_datetime, surface, distance, {馬番:{odds,pop}})。"""
    dt, od = live_odds(rid)
    horses = {str(um): {"odds": v["odds"], "pop": v["pop"]} for um, v in od.items()}
    s = parse_shutuba(rid)   # surface/distance用(キャッシュ可)
    return dt, s.get("surface"), s.get("distance"), horses


def run(date=None, now=None):
    """巡回から呼ぶ。発走15分前に入った未記録レースを全頭スナップショット。"""
    now = now or datetime.datetime.now()
    date = date or now.strftime("%Y%m%d")
    races = _race_list(date)
    log = _load_log(date)
    changed = 0
    for rid, post in races.items():
        if rid in log or not post:
            continue
        hh, mm = map(int, post.split(":"))
        post_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        lead = (post_dt - now).total_seconds() / 60.0
        if not (SNAP_LO <= lead <= SNAP_HI):
            continue
        try:
            dt, surface, distance, horses = _snapshot(rid)
        except Exception as e:
            print(f"[odds_log err] {rid}: {e}")
            continue
        if not horses:   # オッズ未確定(発売前等)はまだ記録しない=次の巡回で再挑戦
            continue
        log[rid] = {"post": post, "lead": round(lead), "official_datetime": dt,
                    "captured_at": now.strftime("%Y-%m-%d %H:%M"),
                    "surface": surface, "distance": distance, "pre": horses}
        changed += 1
    if changed:
        _save_log(date, log)
        print(f"[odds_log] {changed}R スナップショット記録 (計{len(log)}R)")
    return changed


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
    """夜に呼ぶ。記録済み全レースへ確定オッズを付与(post未取得分はスキップ)。"""
    date = date or datetime.date.today().strftime("%Y%m%d")
    log = _load_log(date)
    if not log:
        return 0
    done = 0
    for rid, rec in log.items():
        if rec.get("final"):
            continue
        try:
            fo = _final_odds(rid)
        except Exception as e:
            print(f"[odds_log final err] {rid}: {e}")
            continue
        if fo:
            rec["final"] = fo
            done += 1
    if done:
        _save_log(date, log)
        print(f"[odds_log] {done}R に確定オッズ付与")
    return done


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    races = _race_list(date)
    print(f"race list {date}: {sum(1 for v in races.values() if v)}/{len(races)}R 発走時刻取得")
