#!/usr/bin/env python3
"""【朝】夏戦略・対象レースの発走時刻リストを確定して保存。
5場(小倉/福島/函館/新潟/札幌)の 3歳牝・芝・未勝利 を抽出し、発走時刻を記録。
巡回ジョブ(summer_notify)がこのリストを見て30分前に本命を通知する。

使い方: python3 -m live.summer_schedule [YYYYMMDD]
出力: state/summer_sched_YYYYMMDD.json
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import get_race_ids_for_date, parse_shutuba, fetch, live_odds
from live.summer_notify import prev_run, lin_bonus   # 前走rel/着順/父/キャリア・血統加点を共用
from live import summer_dirt                          # ダート第2戦略の対象判定
from live import summer_shinba                        # 新馬第3戦略(エピ系)の対象判定
from live.sire_lineage_map import LINEAGE, lineage_of
from live import notify
from live import bankroll
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
            rel, fin, sire, n_prev, pstat = prev_run(h["馬ID"], date_iso)
        except Exception:
            rel = fin = sire = pstat = None; n_prev = 0
        out.append({"umaban": h["馬番"], "horse": h["馬名"], "rel": rel, "fin": fin,
                    "lin": lineage_of(sire), "n_prev": n_prev, "pstat": pstat})
    return out


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    date_iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    # 戦略別の稼働期間(MM-DD文字列比較。夏季のみ稼働前提)。
    #   芝・ダート: 6/16〜8/31 (夏ローカル開幕後) / 新馬: 6/1〜 (2歳新馬戦の開始に合わせ前倒し)
    md = date_iso[5:]
    shiba_dirt_on = md >= "06-16"
    shinba_on = md >= "06-01"
    races = []
    race_ids = get_race_ids_for_date(date)
    for rid in race_ids:   # 全会場走査(ダートは全場対象)
        venue = VENUE.get(rid[4:6])
        try:
            s = parse_shutuba(rid)
        except Exception:
            continue
        # 芝戦略(6/16-): 5場 芝 未勝利 3歳牝 / ダート第2戦略(6/16-): 全場 ダ≤1400 未勝利〜OP 牝
        # 新馬第3戦略(6/1-): 全場 芝 2歳新馬 エピ系
        if shiba_dirt_on and venue in LOCAL4 and s["surface"] == "芝" and s["class"] == "未勝利" \
                and any(is_3hinba(h) for h in s["horses"]):
            strat, cands = "shiba", cands_for(s, is_3hinba, date_iso)
        elif shiba_dirt_on and summer_dirt.is_target_race(s):
            strat, cands = "dirt", cands_for(s, summer_dirt.target_horse, date_iso)
        elif shinba_on and summer_shinba.is_target_race(s):
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
                      "race_name": s.get("race_name", ""), "distance": s.get("distance"),
                      "notified": False, "strat": strat, "cands": cands})
    races.sort(key=lambda x: x["post"])
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"summer_sched_{date}.json")
    # ガード: レース一覧(race_ids)自体が0件=netkeiba取得失敗の疑い。既存スケジュールがあれば上書きしない。
    # (レース当日でも全レース対象外で races==0 はあり得るが、その場合 race_ids は非空なので通常通り保存する)
    if not race_ids:
        prev = []
        if os.path.exists(path):
            try:
                prev = json.load(open(path)).get("races", [])
            except Exception:
                prev = []
        if prev:
            warn = (f"⚠️ *夏戦略 スケジュール生成スキップ* {date_iso[5:].replace('-','/')}\n"
                    f"  レース一覧が0件(netkeiba一時失敗の疑い)。既存{len(prev)}Rを保持し上書きを回避しました")
            print(warn); notify.send(warn)
            return
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
    bk = bankroll.load()
    unit = bankroll.daily_unit(date_iso)   # 当日の1点額を朝に凍結
    lines = ["━━━━━━━━━━━━━━",
             f"📅 *夏戦略 対象レース {date_iso[5:].replace('-','/')}* (芝{ns}R / ダ{nd}R / 新馬{nb}R)",
             f"💰 *本日の1点 ¥{unit:,}* (残高¥{bk['balance']:,}×0.5%{('・上限¥'+format(bankroll.CAP,',')) if bankroll.CAP else ''})",
             "_score=馬体重抜きの事前点(score2以上のみ表示)。当日の馬体重+1でscore3=買い目。最終はオッズ・人気込みで発走15分前以内に通知_",
             "_妙味帯=4-12人気かつ単勝10-80倍(ダは10-50倍)。⚠️帯外は人気/オッズ条件を外れ買い目対象外_",
             "_オッズ・人気は朝時点の暫定（未発売は無表示／締切まで大きく変動＝帯判定も変わり得る）_"]
    def odds_str(omap, umaban):   # 朝時点のオッズ・人気(取れた馬のみ)
        lo = omap.get(umaban)
        return f" → {lo['odds']}倍{lo['pop']}人気" if lo else ""
    for r in races:
        rn = r.get("race_name", "")
        dist = f"{r['distance']}m" if r.get("distance") else ""
        try:
            _, omap = live_odds(r["race_id"])   # 朝の暫定オッズ(未発売なら空)
        except Exception:
            omap = {}
        if r["strat"] == "shinba":   # 新馬エピ系: 構造スコアなし。対象産駒を列挙
            lines.append(f"\n*{r['post']} [新馬]{r['venue']}{r['rno']}R* {rn} {dist}(エピ系全頭買い)")
            for c in r["cands"]:
                lines.append(f"  {c['umaban']}番 {c['horse']} (父{c['sire']}){odds_str(omap, c['umaban'])}")
            continue
        st = struct_shiba if r["strat"] == "shiba" else struct_dirt
        tag = "芝" if r["strat"] == "shiba" else "ダ"
        lines.append(f"\n*{r['post']} [{tag}]{r['venue']}{r['rno']}R* {rn} {dist}")
        # 構造2以上のみ表示(馬体重+1が当日乗ればscore3=買い目に届く候補)。構造1以下は最大2で買えない。
        cands2 = sorted([c for c in r["cands"] if c["n_prev"] >= 2 and st(c) >= 2], key=lambda c: -st(c))
        hi = 80 if r["strat"] == "shiba" else 50   # 妙味帯の単勝上限(芝80/ダ50)。下限10・人気4-12は共通
        def band(c):   # 朝オッズでの妙味帯判定: True=帯内 / False=帯外(人気すぎ等) / None=未発売
            lo = omap.get(c["umaban"])
            if not lo or lo.get("pop") is None or lo.get("odds") is None:
                return None
            return (4 <= lo["pop"] <= 12) and (10 <= lo["odds"] < hi)
        for c in cands2:
            relstr = f"4角{c['rel']:.0%}" if c["rel"] is not None else "4角不明"
            finstr = f"前走{c['fin']}着" if c["fin"] is not None else (c.get("pstat") or "前走?")
            btag = " ⚠️*帯外*" if band(c) is False else ""
            lines.append(f"  [score{st(c)}] {c['umaban']}番 {c['horse']} ({finstr}/{relstr}/{c['lin'] or '血統-'}){odds_str(omap, c['umaban'])}{btag}")
        # 帯外(人気/オッズ)は買えないので除外したうえで判定。score2は当日馬体重+1でscore3になり得る。
        prospects = [c for c in cands2 if band(c) is not False]   # 帯内 or 未発売(=買える可能性が残る)
        if any(st(c) >= 3 for c in prospects):
            pass   # 現時点で買い目あり(帯内のscore3+)→マーク無し
        elif any(st(c) == 2 for c in prospects):
            lines.append("  → 🔶 *買い目は当日の馬体重次第*(帯内のscore2が+1でscore3に届けば成立)")
        else:
            reason = ("score2以上なし" if not cands2
                      else "score3+はあるが人気/オッズ帯外" if any(st(c) >= 3 for c in cands2)
                      else "該当馬すべて人気/オッズ帯外")
            lines.append(f"  → 🚫 *買い目なし*({reason})")
    msg = "\n".join(lines) if races else f"📅 *夏戦略 対象レース {date_iso[5:].replace('-','/')}*\n  対象レースなし"
    print(msg)
    notify.send(msg)
    print(f"[state] {path}")


if __name__ == "__main__":
    main()
