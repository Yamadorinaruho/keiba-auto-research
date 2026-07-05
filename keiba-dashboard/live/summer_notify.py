#!/usr/bin/env python3
"""【巡回】発走15分前以内のレースの本命をSlack通知。
朝に summer_schedule が保存した発走時刻リストを見て、現在時刻が
発走3〜15分前のレースだけ、馬体重・直前オッズ込みのスコアで本命算出→通知。
通知済みフラグをstateに書き戻して二重通知を防ぐ。(3分毎巡回前提)

v2(血統フィルタ decision 182): score機構を撤廃し血統一本に。利益指標で再評価した結果。
母集団: live/strategy_spec.py 参照(仕様の単一情報源)。
  in-sample(2010-25): 128点/年 ROI149% 後期+8,886円/年 +12/16年。フォワード検証中。
使い方: python3 -m live.summer_notify [YYYYMMDD]  (env TZ=Asia/Tokyo 前提)
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import parse_shutuba, parse_horse, fetch, live_odds
from live import notify
from live import bankroll
from live import strategy_spec as spec
from live.strategy_spec import GOOD2, GOOD1, SHIBA_BLOOD   # 血統セット(単一情報源)
from live.sire_lineage_map import LINEAGE, lineage_of
from bs4 import BeautifulSoup

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
# 巡回(*/3)が回るたびに、発走 LEAD_MAX 分以内のレースを毎回通知(オッズ・馬体重を取り直し)。
# = 3分ごとに最新オッズで買い目を送り続ける(dedupなし)。発走が近いほどラベルを締切寄りに。
LEAD_MAX = 20.0
BET_PER = 1000     # フォールバック既定額。本番は bankroll.daily_unit(=残高0.5%/上限2万)を使う
MIN_SCORE = 3      # v1(score版)の名残。v1互換評価(strat_eval --strategy v1)のみで使用


def lin_bonus(lin):
    return 2 if lin in GOOD2 else (1 if lin in GOOD1 else 0)


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
        # 馬体重は .Weight セルを直接読む(例 "468(-8)")。行全体テキストだと "468 (-8)" と
        # 空白が入り従来の正規表現が常に不一致→体重不明になっていた(修正)。
        wcell = tr.select_one(".Weight")
        m = re.match(r"(\d{3})", wcell.get_text(strip=True)) if wcell else None
        if num.isdigit() and m:
            out[int(num)] = int(m.group(1))
    return out


def live_post(race_id):
    """ライブの発走時刻 HH:MM を返す(netkeibaは遅延を反映)。失敗時 None。"""
    try:
        html = fetch(f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}",
                     cache_key=f"shutuba_{race_id}.html", force=True)
        rd = BeautifulSoup(html, "html.parser").select_one(".RaceData01")
        if rd:
            m = re.search(r"(\d{1,2}):(\d{2})発走", rd.get_text(" ", strip=True))
            if m:
                return f"{int(m.group(1)):02d}:{m.group(2)}"
    except Exception:
        pass
    return None


_PSTAT = {"除": "前走除外", "中": "前走中止", "取": "前走取消", "失": "前走失格", "降": "前走降着"}


def prev_run(horse_id, before_date):
    """前走の (4角/頭数=相対位置, 着順, 父名, 過去出走数, 前走状態) を返す。
    前走状態=着順が非数値(除外/中止/取消/失格等)のときのラベル、通常はNone。
    過去出走数=before_dateより前のレース数(=今走を含めると n_prev+1 戦目)"""
    sire = None
    try:
        sire = parse_horse(horse_id).get("sire") or None
    except Exception:
        pass
    html = fetch(f"https://db.netkeiba.com/horse/result/{horse_id}/",
                 cache_key=f"hresult_{horse_id}.html")
    soup = BeautifulSoup(html, "html.parser")
    t = soup.select_one(".db_h_race_results")
    if not t:
        return None, None, sire, 0, None
    idx = {h.get_text(strip=True): i for i, h in enumerate(t.select("thead th"))}
    rel = fin = pstat = None
    n_prev = 0
    captured = False
    for tr in t.select("tbody tr"):
        tds = [td.get_text(strip=True) for td in tr.select("td")]
        if len(tds) < 10:
            continue
        d = tds[idx.get("日付", 0)].replace("/", "-")
        if not (d and d < before_date):
            continue
        n_prev += 1
        if not captured:  # 最新(=前走)行のみ rel/着順を採用
            captured = True
            raw = tds[idx["着順"]] if "着順" in idx else ""
            fin = int(raw) if raw.isdigit() else None
            if fin is None and raw:   # 着順が非数値=除外/中止/取消/失格など
                pstat = _PSTAT.get(raw[0], f"前走{raw}")
            nrun = int(tds[idx["頭数"]]) if "頭数" in idx and tds[idx["頭数"]].isdigit() else None
            c4 = None
            # 通過順の最終コーナー値を採用。ダ1000等で「11」のように単一値(ハイフン無し)の
            # ケースも拾う(従来は"-"必須で取りこぼし→前付け判定漏れ)。
            if "通過" in idx and tds[idx["通過"]]:
                last = tds[idx["通過"]].split("-")[-1]
                if last.isdigit():
                    c4 = int(last)
            rel = (c4 / nrun) if (c4 and nrun) else None
    return rel, fin, sire, n_prev, pstat


def build_pick(race_id, feats, date_iso):
    """v2(血統フィルタ): 3歳牝×芝未勝利×帯内オッズ×キャリア×血統(条件はstrategy_spec)を全頭買い。
    feats: 朝(summer_schedule)が計算した不変特徴 {馬番: {lin,n_prev,...}}。無ければ直前に父系統を取得。
    score機構は撤廃(2026-06: 利益指標で血統フィルタ一本に方針転換 decision 182)。"""
    s = parse_shutuba(race_id)
    if s["surface"] != "芝" or s["class"] != "未勝利":
        return None
    _, omap = live_odds(race_id)   # 最新の単勝オッズ・人気(AJAX=リロード相当)
    fmap = {f["umaban"]: f for f in (feats or [])}
    buys = []
    for h in s["horses"]:
        sa = h.get("性齢", "")
        if not (sa.startswith("牝") and sa.endswith("3")):
            continue
        lo = omap.get(h["馬番"])
        pop = lo["pop"] if lo else h.get("人気")
        odds = lo["odds"] if lo else h.get("単勝オッズ")
        if odds is None or not (spec.SHIBA_BAND[0] <= odds < spec.SHIBA_BAND[1]):   # 人気は不問
            continue
        f = fmap.get(h["馬番"])
        if f is not None:   # 朝に計算済みの不変特徴を利用
            lin, n_prev = f["lin"], f["n_prev"]
        else:               # フォールバック: 未計算なら直前に父系統取得
            _, _, sire, n_prev, _ = prev_run(h["馬ID"], date_iso) if h.get("馬ID") else (None, None, None, 0, None)
            lin = lineage_of(sire)
        if n_prev < spec.MIN_CAREER:   # 3走目以上(過去出走2戦以上)
            continue
        if lin not in SHIBA_BLOOD:   # 血統フィルタ
            continue
        buys.append({"馬番": h["馬番"], "馬名": h["馬名"], "人気": pop, "odds": odds, "lin": lin})
    if not buys:
        return None
    buys.sort(key=lambda x: -x["odds"])
    return {"race_name": s["race_name"], "distance": s["distance"], "buys": buys}


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
    unit = bankroll.daily_unit(date_iso)   # 当日の1点額(芝ダ=残高0.5%・朝に凍結)
    unit_shinba = bankroll.daily_unit(date_iso, strat="shinba")   # 新馬のみ残高1.0%
    now = now_jst()
    changed = False
    blocks = []   # 窓内レースを1巡回=1メッセージに集約(時間が被っても1通)
    for r in sched["races"]:
        hh, mm = map(int, r["post"].split(":"))
        post_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        lead = (post_dt - now).total_seconds() / 60.0  # 発走まで何分
        # 発走が近いレースはライブ発走時刻を再取得して遅延を反映(締切前に正しい3分前で投票するため)
        if 0 < lead <= 40:
            lp = live_post(r["race_id"])
            if lp and lp != r["post"]:
                r["post"] = lp; changed = True
                hh, mm = map(int, lp.split(":"))
                post_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                lead = (post_dt - now).total_seconds() / 60.0
        # 発走LEAD_MAX分以内なら毎回通知(*/3が回るたび=3分ごと、dedupなし)
        if not (0 < lead <= LEAD_MAX):
            continue
        lead_i = int(round(lead))
        tier = ("🔔 *締切直前・最終買い目*" if lead <= 5 else
                "🕐 *発走間近・買い目*" if lead <= 12 else
                "📣 *速報・買い目*")
        hdr = f"{tier} (発走{lead_i}分前)"
        if r.get("strat") in ("dirt", "shinba"):   # ダート第2/新馬第3戦略は専用処理に委譲
            try:
                if r.get("strat") == "dirt":
                    from live import summer_dirt
                    text, picks = summer_dirt.process_race(r, date_iso, lead_i, unit)
                else:
                    from live import summer_shinba
                    text, picks = summer_shinba.process_race(r, date_iso, lead_i, unit_shinba)
            except Exception as e:
                print(f"[err-{r.get('strat')}] {r['race_id']}: {e}")
                continue
            r["picks"] = picks   # 毎回上書き(締切に近い買い目を収支に使う)
            changed = True
            if text:
                blocks.append(hdr + "\n" + text)
            else:
                print(f"[{r.get('strat')}] {r['venue']}{r['rno']}R → 買い目なし(発走{lead_i}分前)")
            continue
        try:
            p = build_pick(r["race_id"], r.get("cands"), date_iso)
        except Exception as e:
            print(f"[err] {r['race_id']}: {e}")
            continue
        changed = True
        if not p:
            r["picks"] = []
            continue
        # v2: 血統フィルタ該当を全頭買い(score撤廃 decision 182)
        buys = p["buys"]
        r["picks"] = [{"umaban": c["馬番"], "horse": c["馬名"], "odds_pre": c["odds"], "lin": c["lin"]} for c in buys]
        head = (f"{hdr}\n"
                f"🏇 *{r['venue']}{r['rno']}R* {p['race_name']} ({p['distance']}m)\n"
                f"⏱ 発走 {r['post']} → *発走{lead_i}分前*")
        lines = [head,
                 "━━━━━━━━━━━━━━",
                 f"🎯 *買い目: 単勝 各¥{unit:,} (計¥{unit*len(buys):,})*"]
        for c in buys:
            lines.append(f"  ▶ *{c['馬番']}番 {c['馬名']}* ({c['人気']}人気 {c['odds']}倍 / 父系{c['lin']})")
        lines += ["━━━━━━━━━━━━━━",
                  f"_血統(ディープ/サンデー他/カナロア)×単勝{spec.band_str(spec.SHIBA_BAND)}×3走目以上を全頭。オッズは発走{lead_i}分前時点(変動)_"]
        blocks.append("\n".join(lines))
    if blocks:
        DIV = "━━━━━━━━━━━━━━"
        header = f"{DIV}\n🐎 *夏戦略 買い目 {now.strftime('%H:%M')}時点* (発走20分以内 {len(blocks)}R・3分毎更新)"
        msg = header + "\n" + (f"\n{DIV}\n".join(blocks))
        print(msg)
        notify.send(msg)
    if changed:
        json.dump(sched, open(path, "w"), ensure_ascii=False, indent=1)
        print("[state] updated")
    # JRA全レースの発走15分前オッズ記録(較正用・戦略とは独立。失敗しても通知に影響させない)
    try:
        from live import odds_log
        odds_log.run(date)
    except Exception as e:
        print(f"[odds_log skip] {e}")


if __name__ == "__main__":
    main()
