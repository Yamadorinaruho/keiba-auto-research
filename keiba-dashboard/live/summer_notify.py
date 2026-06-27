#!/usr/bin/env python3
"""【巡回】発走15分前以内のレースの本命をSlack通知。
朝に summer_schedule が保存した発走時刻リストを見て、現在時刻が
発走3〜15分前のレースだけ、馬体重・直前オッズ込みのスコアで本命算出→通知。
通知済みフラグをstateに書き戻して二重通知を防ぐ。(3分毎巡回前提)

スコア(decision 159/168): 前走4角中団以降(>0.33) + 前走6着以下 + 馬体重420-470
  + 妙味血統(ディープ系+2 / サンデー系他・カナロア系+1)  ※外枠軸は死に軸で除外
母集団: 3歳牝 芝 未勝利 4-12番人気 単勝10-70倍(30分前) (血統除外なし) decision 169
  狙いは確定 人気4-12×単勝10-80。上向きドリフト(中央+24%)補正で選択上限を70に。
  ※単勝10倍未満は"名目4-12番人気でも実は人気馬(織り込み済み)"なので除外
  かつ 過去出走2戦以上(3戦目以上)。1-2戦目は見限り妙味が未成熟で除外 (decision 160)
使い方: python3 -m live.summer_notify [YYYYMMDD]  (env TZ=Asia/Tokyo 前提)
"""
import sys, os, re, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import parse_shutuba, parse_horse, fetch, live_odds
from live import notify
from live import bankroll
from live.sire_lineage_map import LINEAGE, lineage_of
from bs4 import BeautifulSoup

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
# 巡回(*/3)が回るたびに、発走 LEAD_MAX 分以内のレースを毎回通知(オッズ・馬体重を取り直し)。
# = 3分ごとに最新オッズで買い目を送り続ける(dedupなし)。発走が近いほどラベルを締切寄りに。
LEAD_MAX = 20.0
BET_PER = 1000     # フォールバック既定額。本番は bankroll.daily_unit(=残高0.5%/上限2万)を使う
MIN_SCORE = 3      # この点以上の該当馬を全部買う (decision 156)
# 血統加点 (decision 158/167): 最終形フィルタ下の母集団ROIで格付け。
# ディープ系155%=+2, サンデー系他134%/カナロア系149%=+1, 他(米国系95%含む<100%)=0。除外なし。
GOOD2 = {"ディープ系"}                  # +2
GOOD1 = {"サンデー系他", "カナロア系"}   # +1 (米国系は最終形フィルタ下で95%=加点根拠消失のため除外 decision 167)


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


def axes(c):
    rel = c["rel"]
    lin = c.get("lin")
    b = lin_bonus(lin)
    return [("前走中団以降", rel is not None and rel > 0.33, f"4角{rel:.0%}" if rel is not None else "4角不明"),
            ("前走6着以下", c["前着"] is not None and c["前着"] >= 6, f"前走{c['前着']}着" if c["前着"] is not None else (c.get("pstat") or "前走不明")),
            ("馬体重420-470", c["体重"] is not None and 420 <= c["体重"] <= 470, f"{c['体重']}kg" if c["体重"] is not None else "体重不明"),
            (f"妙味血統+{b}", b > 0, f"{lin or '血統不明'}(+{b})")]


def reason(c):
    lbl = {"前走中団以降": "前走で脚を余す", "前走6着以下": "前走負けて人気落ち",
           "馬体重420-470": "好適馬体重"}
    a = axes(c)
    ok = [lbl[n] for n, hit, _ in a[:3] if hit]
    lin, b = c.get("lin"), lin_bonus(c.get("lin"))
    if b:
        ok.append(f"{lin}(妙味血統+{b})")
    return " / ".join(ok) if ok else "母集団該当のみ"


def build_pick(race_id, feats, date_iso):
    """feats: 朝(summer_schedule)が計算した不変特徴 {馬番: {rel,fin,lin,n_prev}}。
    出馬表からは変動する 馬体重・オッズ・人気 のみ取得して合成する。"""
    s = parse_shutuba(race_id)
    if s["surface"] != "芝" or s["class"] != "未勝利":
        return None
    wmap = get_weight(race_id)
    _, omap = live_odds(race_id)   # 最新の単勝オッズ・人気(AJAX=リロード相当)
    fmap = {f["umaban"]: f for f in (feats or [])}
    cands = []
    for h in s["horses"]:
        sa = h.get("性齢", "")
        if not (sa.startswith("牝") and sa.endswith("3")):
            continue
        lo = omap.get(h["馬番"])
        pop = lo["pop"] if lo else h.get("人気")
        odds = lo["odds"] if lo else h.get("単勝オッズ")
        if pop is None or odds is None or not (4 <= pop <= 12) or not (10 <= odds < 80):
            continue
        wt = wmap.get(h["馬番"])
        f = fmap.get(h["馬番"])
        if f is not None:   # 朝に計算済みの不変特徴を利用
            rel, fin, lin, n_prev, pstat = f["rel"], f["fin"], f["lin"], f["n_prev"], f.get("pstat")
        else:               # フォールバック: 未計算なら直前に取得
            rel, fin, sire, n_prev, pstat = prev_run(h["馬ID"], date_iso) if h.get("馬ID") else (None, None, None, 0, None)
            lin = lineage_of(sire)
        if n_prev < 2:   # 1-2戦目(過去出走0-1)は見限り妙味が未成熟なため除外 (decision 160)
            continue
        sc = (int(rel is not None and rel > 0.33)
              + int(fin is not None and fin >= 6) + int(wt is not None and 420 <= wt <= 470)
              + lin_bonus(lin))
        cands.append({"馬番": h["馬番"], "馬名": h["馬名"], "人気": pop, "odds": odds,
                      "rel": rel, "前着": fin, "体重": wt, "lin": lin, "pstat": pstat, "score": sc})
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
    unit = bankroll.daily_unit(date_iso)   # 当日の1点額(残高0.5%/上限2万・朝に凍結)
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
                    text, picks = summer_shinba.process_race(r, date_iso, lead_i, unit)
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
        # score>=3 の該当馬を全部買う (cc-memory decision 156)
        allc = [p["honmei"]] + p["others"]
        buys = [c for c in allc if c["score"] >= MIN_SCORE]
        r["picks"] = [{"umaban": c["馬番"], "horse": c["馬名"], "odds_pre": c["odds"], "score": c["score"]} for c in buys]
        head = (f"{hdr}\n"
                f"🏇 *{r['venue']}{r['rno']}R* {p['race_name']} ({p['distance']}m)\n"
                f"⏱ 発走 {r['post']} → *発走{lead_i}分前*")
        if not buys:
            print(f"{head}  → 買い目なし(最高score {allc[0]['score']})")
            continue
        lines = [head,
                 "━━━━━━━━━━━━━━",
                 f"🎯 *買い目: 単勝 各¥{unit:,} (計¥{unit*len(buys):,})*"]
        for c in buys:
            lines.append(f"  ▶ *{c['馬番']}番 {c['馬名']}*")
        lines += [
                 "━━━━━━━━━━━━━━",
                 f"_オッズ・人気は発走{lead_i}分前時点（締切まで変動します）_",
                 "▼内訳"]
        for c in buys:
            ax = " ".join(f"{'✅' if hit else '⬜'}{n}({v})" for n, hit, v in axes(c))
            lines.append(f"・{c['馬番']}番 *{c['馬名']}* {c['人気']}人気 *{c['odds']}倍* (score {c['score']}/5)")
            lines.append(f"   {ax} → {reason(c)}")
        skipped = [c for c in allc if c["score"] < MIN_SCORE]
        if skipped:
            lines.append("_見送り(score<3): " + " , ".join(f"{c['馬番']}{c['馬名']}(s{c['score']})" for c in skipped) + "_")
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
