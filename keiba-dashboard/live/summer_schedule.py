#!/usr/bin/env python3
"""【朝】夏戦略・対象レースの発走時刻リストを確定して保存。
5場(小倉/福島/函館/新潟/札幌)の 3歳牝・芝・未勝利 を抽出し、発走時刻を記録。
巡回ジョブ(summer_notify)がこのリストを見て30分前に本命を通知する。

使い方: python3 -m live.summer_schedule [YYYYMMDD]
出力: state/summer_sched_YYYYMMDD.json
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import get_race_ids_for_date, parse_shutuba, fetch, live_odds, parse_horse
from live.summer_notify import prev_run, lin_bonus   # 前走rel/着順/父/キャリア・血統加点を共用
from live import strategy_spec as spec                # 仕様の単一情報源(帯・血統・窓)
from live import summer_dirt                          # ダート第2戦略の対象判定
from live import summer_shinba                        # 新馬第3戦略(エピ系)の対象判定
from live.sire_lineage_map import LINEAGE, lineage_of, lineage_of_line
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
        # 血統判定: 父で引けなければ男系ライン(父父→)を遡って自動判定(2026-07-11)。
        # ped ページは prev_run で取得済み=キャッシュヒットのため追加リクエストなし。
        lin = lineage_of(sire)
        lin_via = None
        if lin is None and sire:
            try:
                sline = parse_horse(h["馬ID"]).get("sire_line") or []
                lin, lin_via = lineage_of_line(sire, sline)
            except Exception:
                pass
        out.append({"umaban": h["馬番"], "horse": h["馬名"], "rel": rel, "fin": fin,
                    "lin": lin, "lin_via": lin_via, "sire": sire, "n_prev": n_prev, "pstat": pstat})
    return out


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    date_iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    # 前日プレビュー: 未来日付を渡されたら「前日20時の予告通知」モード(bankroll凍結はしない)
    preview = date > datetime.date.today().strftime("%Y%m%d")
    # 戦略別の稼働期間はstrategy_spec.WINDOWSに一元定義(芝ダ6/16-8/31・新馬6/1-8/31)。
    shiba_dirt_on = spec.in_window("shiba", date)
    shinba_on = spec.in_window("shinba", date)
    races = []
    race_ids = get_race_ids_for_date(date)
    # 毎日20時プレビュー化(2026-07-12): 翌日にJRA開催が無い夜は黙ってスキップ
    # (火〜木の夜などに「対象レースなし」をSlackに流さない。開催日で対象ゼロの場合は従来通り通知する)
    if preview and not race_ids:
        print(f"[preview skip] {date_iso} は開催なし(レース一覧0件)。通知せず終了")
        return
    for rid in race_ids:   # 全会場走査(ダートは全場対象)
        venue = VENUE.get(rid[4:6])
        try:
            s = parse_shutuba(rid)
        except Exception:
            continue
        # 芝戦略(6/16-): 5場 芝 未勝利 3歳牝 / ダート第2戦略(6/16-): 全場 ダ≤1400 未勝利〜OP 牝
        # 新馬第3戦略(6/1-): 全場 芝 2歳新馬 エピ系
        if shiba_dirt_on and s["surface"] == "芝" and s["class"] == "未勝利" \
                and any(is_3hinba(h) for h in s["horses"]):   # v2: 全会場(純ローカル限定を撤廃 decision 182)
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
    # v2(血統フィルタ decision 182): 対象血統×3走目以上を全頭買い。score機構は撤廃。
    def blood_ok(c, strat):
        if c["n_prev"] < spec.MIN_CAREER:   # 3走目以上(過去出走2戦以上)
            return False
        return c["lin"] in (spec.SHIBA_BLOOD if strat == "shiba" else spec.DIRT_BLOOD)
    ns = sum(r["strat"] == "shiba" for r in races); nd = sum(r["strat"] == "dirt" for r in races)
    nb = sum(r["strat"] == "shinba" for r in races)
    bk = bankroll.load()
    unit = bankroll.daily_unit(date_iso, freeze=not preview)   # 当日の1点額(朝に凍結・前日は参考値)
    unit_sb = bankroll.daily_unit(date_iso, freeze=not preview, strat="shinba")   # 新馬単勝 残高1.0%(v3 2026-07-12〜)
    unit_sw = bankroll.daily_unit(date_iso, freeze=not preview, strat="shinba_wide")   # 新馬ワイド1ペア 0.5%(手動)
    head = f"📅 *夏戦略 {'明日' if preview else ''}対象レース {date_iso[5:].replace('-','/')}*" \
           + (" (前日20時プレビュー)" if preview else "")
    lines = ["━━━━━━━━━━━━━━",
             f"{head} (芝{ns}R / ダ{nd}R / 新馬{nb}R)",
             f"💰 *1点 芝ダ¥{unit:,} / 新馬単¥{unit_sb:,} / 新馬ワイド¥{unit_sw:,}* (残高¥{bk['balance']:,}×{bankroll.FRAC:.1%}/{bankroll.SHINBA_FRAC:.1%}/{bankroll.SHINBA_WIDE_FRAC:.1%}{('・上限¥'+format(bankroll.CAP,',')) if bankroll.CAP else ''})",
             "_v2血統フィルタ: 対象血統(芝=ディープ/サンデー他/カナロア・ダ=米国系)×3走目以上を全頭買い。score無し_",
             f"_買い目は単勝オッズ帯(芝{spec.band_str(spec.SHIBA_BAND)}/ダ{spec.band_str(spec.DIRT_BAND)})を発走15分前以内に判定。⚠️帯外は対象外_",
             "_オッズ・人気は朝時点の暫定（未発売は無表示／締切まで変動＝帯判定も変わり得る）_"]
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
        if r["strat"] == "shinba":   # 新馬v3: 単勝=エピ牡+エフ+シスキン / ワイドBOX=対象2頭以上(牡牝込み)
            n_wide = len(r["cands"]) * (len(r["cands"]) - 1) // 2 if len(r["cands"]) >= 2 else 0
            wtag = f"・ワイドBOX{n_wide}点" if n_wide else ""
            lines.append(f"\n*{r['post']} [新馬]{r['venue']}{r['rno']}R* {rn} {dist}(単勝全頭{wtag})")
            for c in r["cands"]:
                tag = "" if c.get("win", True) else " (ワイドのみ)"
                lines.append(f"  {c['umaban']}番 {c['horse']} (父{c['sire']}){odds_str(omap, c['umaban'])}{tag}")
            continue
        tag = "芝" if r["strat"] == "shiba" else "ダ"
        lo_band, hi_band = (15, 80) if r["strat"] == "shiba" else (10, 80)
        lines.append(f"\n*{r['post']} [{tag}]{r['venue']}{r['rno']}R* {rn} {dist}")
        elig = [c for c in r["cands"] if blood_ok(c, r["strat"])]
        if not elig:
            lines.append("  → 🚫 *対象血統(3走目以上)なし*")
            continue
        def band(c):   # 朝オッズでの帯判定: True=帯内 / False=帯外 / None=未発売
            lo = omap.get(c["umaban"])
            if not lo or lo.get("odds") is None:
                return None
            return lo_band <= lo["odds"] < hi_band
        for c in elig:
            btag = " ⚠️*帯外*" if band(c) is False else ""
            lines.append(f"  {c['umaban']}番 {c['horse']} (父系{c['lin'] or '不明'}){odds_str(omap, c['umaban'])}{btag}")
        if all(band(c) is False for c in elig):
            lines.append(f"  → 🔶 *現時点は全頭オッズ帯外({lo_band}-{hi_band}倍)・締切まで変動*")
    # 系統マップ未収録の警告: キャリア条件は満たすのに血統判定できない馬は買い目から黙って漏れるため明示する
    # (男系ライン自動判定でも解決しなかった馬のみ。自動判定できた馬は監査用に別途知らせる)
    unknown = []; auto = []
    for r in races:
        if r["strat"] == "shinba":
            continue
        for c in r["cands"]:
            if c.get("lin") is None and c.get("n_prev", 0) >= spec.MIN_CAREER:
                sire = c.get("sire") or "前走取得失敗"
                unknown.append(f"・{r['venue']}{r['rno']}R {c['horse']} (父{sire}・{c['n_prev']+1}走目)")
            elif c.get("lin_via"):
                auto.append(f"・{r['venue']}{r['rno']}R {c['horse']} 父{c.get('sire')}→{c['lin']}(父祖{c['lin_via']}経由の自動判定)")
    if auto:
        lines += ["", "ℹ️ *系統を男系ラインから自動判定* (マップ未収録の新種牡馬。判定根拠を確認可)"] + auto
    if unknown:
        lines += ["", "⚠️ *系統マップ未収録の候補馬* (血統判定不能→対象外扱いになる。要分類確認)"] + unknown
    msg = "\n".join(lines) if races else f"📅 *夏戦略 {'明日' if preview else ''}対象レース {date_iso[5:].replace('-','/')}*\n  対象レースなし"
    print(msg)
    notify.send(msg)
    print(f"[state] {path}")


if __name__ == "__main__":
    main()
