#!/usr/bin/env python3
"""【戦略フォワード評価】指定日の買い目を確定オッズ・結果で集計しROIを出す。v1/v2対応。
結果ページをスクレイプ(確定単勝オッズ・着順)し、prev_run/parse_shutubaで各戦略の条件を判定。
1点100円換算のフラットROI(複利・ステーキング無視=純粋な戦略性能)。キャッシュ利用で再実行は高速。

戦略:
  v2(既定) = 血統フィルタ。芝:全会場×芝未勝利×3歳牝×15-80倍×3走目以上×父{ディープ/サンデー他/カナロア}
            ダ:全場×ダ≤1400×未勝利〜OP×3歳牝×10-80倍×3走目以上×父米国系 / 新馬:芝2歳新馬×エピ系
  v1       = scoreフィルタ(旧)。芝:純ローカル5場×芝未勝利×3歳牝×人気4-12×10-80倍×3走目以上×score≥3
            (score=前走中団以降+前走6着以下+馬体重420-470+血統ディープ+2/サンデー他カナロア+1)
            ダ:全場×ダ≤1400×未勝利〜OP×牝(全年齢)×人気4-12×10-50倍×3走目以上×score≥3
            (score=前付け+米国系+馬体重450-490+前走9着以内) / 新馬:v2と同じ
  ※人気は確定オッズ順位で算出(通知時人気とは厳密には異なる)。

戦略は「引数のプリセット」。v1/v2はSTRATEGIESレジストリに定義済み。
ライブ投票コードとは独立(=確定オッズで後付け評価する専用ツール)。新戦略の試し方は2通り:
  (A) その場で引数指定(コード不要・推奨) → 下記の帯/血統/キャリア/人気帯フラグを渡すと
      v2をベースに上書きした即席戦略で評価。組合せごとに別キャッシュ。
  (B) 恒久登録 → make_blood_filter(...)をSTRATEGIESに1行追加(v1のscore系はmatch関数を書く)。

使い方:
  python3 -m live.strat_eval                                  # 今日・v2
  python3 -m live.strat_eval --season                         # シーズン全開催日(6/1〜今日)を一括評価+累計サマリ
  python3 -m live.strat_eval --summary                        # キャッシュ済み日の累計サマリのみ(未評価日を警告)
  python3 -m live.strat_eval --list                           # 登録済みプリセット一覧
  python3 -m live.strat_eval 20260628 --strategy v1           # v1プリセット
  python3 -m live.strat_eval 20260620 20260627 20260628       # 複数日(合算)
  python3 -m live.strat_eval 20260628 --detail                # 馬ごと明細
  ※サマリは戦略ごとの稼働窓(芝ダ6/16-8/31・新馬6/1-8/31)内だけを本評価とし、
    窓外は「参考」として別掲する。撤退基準(strategy_spec.STOP_RULES)の判定付き。
  # ── 引数で即席戦略(v2をベースに上書き) ──
  python3 -m live.strat_eval 20260620 20260627 --band-shiba 15-50    # 芝の帯を15-50に
  python3 -m live.strat_eval 20260620 --band-dirt 10-50 --career 3   # ダ10-50・4走目以上
  python3 -m live.strat_eval 20260620 --blood-shiba ディープ系       # 芝をディープ系のみ
  python3 -m live.strat_eval 20260620 --pop 4-12                     # 確定人気4-12に限定
フラグ: --band-shiba LO-HI / --band-dirt LO-HI / --blood-shiba a,b,c /
        --blood-dirt a,b / --career N / --pop LO-HI
"""
import sys, os, re, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import parse_shutuba, get_race_ids_for_date
from live.summer_notify import prev_run, get_weight, lin_bonus
from live.summer_shinba import horse_sire
from live.summer_settle import result
from live import strategy_spec as spec
from live.sire_lineage_map import lineage_of
import live.summer_dirt as sd

SHIBA_BLOOD = spec.SHIBA_BLOOD
EPI = spec.SHINBA_SIRES
US = spec.DIRT_BLOOD
LOCAL5 = {"01", "02", "03", "04", "10"}   # 札幌/函館/福島/新潟/小倉(netkeiba場コード)
UNIT = 100
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "state", "strat_eval_cache")


def _is_3hinba(sa):
    return sa.startswith("牝") and sa.endswith("3")


def _epi(sire):
    s = sire or ""
    if s in EPI:
        return True
    m = re.match(r"^[^\x00-\x7f]+", s)
    return (m.group(0) if m else s) in EPI


def _pop_map(fo):
    """確定オッズ {馬番:odds} → 人気順位 {馬番:rank}。"""
    ranked = sorted(fo.items(), key=lambda kv: kv[1])
    return {um: i + 1 for i, (um, od) in enumerate(ranked)}


def _strat_of(surf, cls, dist):
    if surf == "芝" and cls == "新馬":
        return "shinba"
    if surf == "芝" and cls == "未勝利":
        return "shiba"
    if surf == "ダ" and cls in sd.CLS_DIRT and dist and dist <= sd.MAX_DIST:
        return "dirt"
    return None


# ─────────────────────────────────────────────────────────────────────────
# 戦略レジストリ
#   各戦略は Strat(match, needs_weight, desc)。match(strat, h, od, pop, di,
#   place_code, wmap)->bool が「その馬を買うか」を返す(strat=shiba/dirt/shinba)。
#   新戦略の追加方法:
#     (A) v2系(3歳牝×血統×オッズ帯×キャリア)の派生 → make_blood_filter(...) を
#         STRATEGIES に1行登録するだけ(バンド・血統セット・キャリア下限を変えるだけ)。
#     (B) v1のような独自ロジック → match関数を書いて Strat(...) で登録。
#         馬体重が要るなら needs_weight=True(eval_dayがget_weightを引いてwmapに渡す)。
# ─────────────────────────────────────────────────────────────────────────
class Strat:
    def __init__(self, match, needs_weight=False, desc=""):
        self.match = match
        self.needs_weight = needs_weight
        self.desc = desc


def _shinba_match(h):
    """新馬·エピ系(全戦略共通): 父系がエピファネイア/エフフォーリア × 牡のみ(2026-07-10〜, spec v2.1)。"""
    if not (h.get("性齢") or "").startswith(spec.SHINBA_GENDER):
        return False
    return bool(h.get("馬ID")) and _epi(horse_sire(h["馬ID"]))


def make_blood_filter(shiba_band, dirt_band, shiba_blood, dirt_blood,
                      min_career=2, require_3hinba=True, pop_range=None):
    """v2系の血統フィルタ戦略を生成。芝/ダで別々のオッズ帯・血統セットを指定。
    band=(lo, hi) は lo<=odds<hi。min_career=過去走数の下限(2=3走目以上)。
    pop_range=(lo,hi) を渡すと確定オッズ人気順位でlo<=人気<=hiに絞る(Noneで無制限)。"""
    def match(strat, h, od, pop, di, place_code, wmap):
        if strat == "shinba":
            return _shinba_match(h)
        if not h.get("馬ID"):
            return False
        if require_3hinba and not _is_3hinba(h.get("性齢", "")):
            return False
        if pop_range:
            p = pop.get(h.get("馬番"))
            if p is None or not (pop_range[0] <= p <= pop_range[1]):
                return False
        band = shiba_band if strat == "shiba" else dirt_band
        if not (band[0] <= od < band[1]):
            return False
        try:
            _, _, sire, n_prev, _ = prev_run(h["馬ID"], di)
        except Exception:
            return False
        if n_prev < min_career:
            return False
        lin = lineage_of(sire)
        return lin in (shiba_blood if strat == "shiba" else dirt_blood)
    return match


def _v1_match(strat, h, od, pop, di, place_code, wmap):
    """v1(旧): score≥3フィルタ。芝=純ローカル5場×3歳牝×人気4-12×10-80倍、
    ダ=全場×牝(全年齢)×人気4-12×10-50倍。scoreは前走/血統/馬体重から算出。"""
    if strat == "shinba":
        return _shinba_match(h)
    if not h.get("馬ID"):
        return False
    sa = h.get("性齢", "")
    p = pop.get(h.get("馬番"))
    if p is None or not (4 <= p <= 12):
        return False
    if strat == "shiba":
        if place_code not in LOCAL5 or not _is_3hinba(sa) or not (10 <= od < 80):
            return False
    else:  # dirt: 牝(全年齢)
        if not sa.startswith("牝") or not (10 <= od < 50):
            return False
    try:
        rel, finprev, sire, n_prev, _ = prev_run(h["馬ID"], di)
    except Exception:
        return False
    if n_prev < 2:
        return False
    wt = wmap.get(h.get("馬番"))
    lin = lineage_of(sire)
    if strat == "shiba":
        sc = (int(rel is not None and rel > 0.33) + int(finprev is not None and finprev >= 6)
              + int(wt is not None and 420 <= wt <= 470) + lin_bonus(lin))
    else:
        sc = (int(rel is not None and rel <= 0.33) + int(lin in US)
              + int(wt is not None and 450 <= wt <= 490) + int(finprev is not None and finprev <= 9))
    return sc >= 3


STRATEGIES = {
    "v2": Strat(make_blood_filter(spec.SHIBA_BAND, spec.DIRT_BAND, SHIBA_BLOOD, US,
                                  min_career=spec.MIN_CAREER),
                desc=f"血統フィルタ(現行). 芝{spec.band_str(spec.SHIBA_BAND)}/"
                     f"ダ{spec.band_str(spec.DIRT_BAND)}・3歳牝・3走目以上"),
    "v1": Strat(_v1_match, needs_weight=True,
                desc="scoreフィルタ(旧). 人気4-12・score≥3・馬体重使用"),
    # 派生例(コメントアウト): バンドや血統セットを変えるだけで新戦略を追加できる。
    # "v2narrow": Strat(make_blood_filter((15, 50), (10, 50), SHIBA_BLOOD, US),
    #                   desc="v2のオッズ帯を50倍上限に絞った版"),
    # "v2deeponly": Strat(make_blood_filter((15, 80), (10, 80), {"ディープ系"}, US),
    #                     desc="芝をディープ系のみに限定"),
}


def _cache_path(day, strategy):
    return os.path.join(CACHE_DIR, f"{day}_{strategy}.tsv")


_CACHE_COLS = ("race_id", "strat", "umaban", "horse", "odds", "win")


def _read_cache(cp):
    """キャッシュ読込。先頭の `# spec=` 行があれば現行仕様のフィンガープリントと照合し、
    不一致(=仕様変更後の古い評価)は ValueError でミス扱いにして再評価させる。
    メタ行なし(移行前の旧キャッシュ)は現行仕様生成とみなして受け入れる。"""
    lines = open(cp, encoding="utf-8").read().splitlines()
    if lines and lines[0].startswith("#"):
        m = re.search(r"spec=(\w+)", lines[0])
        if m and m.group(1) != spec.fingerprint():
            raise ValueError(f"仕様変更によりキャッシュ無効: {cp}")
        lines = lines[1:]
    rows = []
    for ln in lines[1:]:   # 先頭はカラムヘッダ
        rid, strat, um, horse, od, win = ln.split("\t")
        rows.append((rid, strat, int(um), horse, float(od), win == "1"))
    return rows


def _write_cache(cp, picks):
    lines = [f"# spec={spec.fingerprint()} ver={spec.SPEC_VERSION}",
             "\t".join(_CACHE_COLS)]
    for rid, strat, um, horse, od, win in picks:
        lines.append("\t".join([rid, strat, str(um), horse, str(od), "1" if win else "0"]))
    open(cp, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def eval_day(day, strategy, use_cache=True):
    """過去日(=今日より前)は評価結果をTSVキャッシュし、再実行を一瞬にする。
    結果は確定後不変なので安全。当日はキャッシュしない(オッズ/着順が未確定の可能性)。"""
    today = datetime.date.today().strftime("%Y%m%d")
    cacheable = use_cache and day < today
    cp = _cache_path(day, strategy)
    if cacheable and os.path.exists(cp):
        try:
            return _read_cache(cp)
        except Exception:
            pass
    picks = _eval_day_uncached(day, strategy)
    if cacheable:
        os.makedirs(CACHE_DIR, exist_ok=True)
        _write_cache(cp, picks)
    return picks


def _eval_day_uncached(day, strategy):
    spec = STRATEGIES[strategy]
    di = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    # 過去日のresultページは確定後不変なのでHTMLキャッシュ利用(force=False)。
    # 当日のみ最新取得(force=True)。即席戦略で同じ過去日を何度試してもresultを再DLしない。
    fresh = day >= datetime.date.today().strftime("%Y%m%d")
    picks = []
    for rid in get_race_ids_for_date(day):
        try:
            s = parse_shutuba(rid)
        except Exception:
            continue
        strat = _strat_of(s.get("surface"), s.get("class"), s.get("distance"))
        if not strat:
            continue
        try:
            fin, pay, fo = result(rid, force=fresh)
        except Exception:
            continue
        if not fo:
            continue
        pop = _pop_map(fo)
        wmap = get_weight(rid) if (spec.needs_weight and strat != "shinba") else {}
        for h in s["horses"]:
            um = h.get("馬番")
            od = fo.get(um)
            if od is None:
                continue
            if spec.match(strat, h, od, pop, di, rid[4:6], wmap):
                picks.append((rid, strat, um, h.get("馬名", ""), od, fin.get(um) == 1))
    return picks


def summarize(picks):
    n = len(picks)
    hits = sum(1 for p in picks if p[5])
    ret = sum(p[4] * UNIT for p in picks if p[5])
    stake = n * UNIT
    return n, hits, stake, ret, (ret / stake * 100 if stake else 0)


# ─────────────────────────────────────────────────────────────────────────
# シーズン累計サマリ (--season / --summary)
#   窓内(strategy_spec.WINDOWS)のピックだけを本評価とし、窓外は参考として別掲。
#   撤退基準(strategy_spec.STOP_RULES)は窓内実測で自動判定する。
# ─────────────────────────────────────────────────────────────────────────
_LEG_JA = {"shiba": "芝", "dirt": "ダート", "shinba": "新馬"}


def _season_days():
    """当年シーズン開始(6/1)〜min(今日, 8/31) の日付リスト(YYYYMMDD)。"""
    today = datetime.date.today()
    y = today.year
    start = datetime.date(y, int(spec.SEASON_START[:2]), int(spec.SEASON_START[3:]))
    end = min(today, datetime.date(y, int(spec.SEASON_END[:2]), int(spec.SEASON_END[3:])))
    days, d = [], start
    while d <= end:
        days.append(d.strftime("%Y%m%d"))
        d += datetime.timedelta(days=1)
    return days


def summary_report(picks_by_day, strategy):
    legs = ("shiba", "dirt", "shinba")
    inw = {l: [] for l in legs}
    outw = {l: [] for l in legs}
    for day, picks in picks_by_day.items():
        for p in picks:
            (inw if spec.in_window(p[1], day) else outw)[p[1]].append(p)
    print(f"\n== シーズン累計サマリ ({strategy} / 窓内のみ本評価 / 1点{UNIT}円) ==")
    print(f"{'戦略':<7}{'稼働窓':<16}{'点':>4}{'的中':>4}{'的中率':>7}{'投資':>10}{'払戻':>10}{'ROI':>6}")

    def row(name, window, picks):
        n, h, stake, ret, roi = summarize(picks)
        rate = f"{h / n * 100:.1f}%" if n else "-"
        print(f"{name:<7}{window:<16}{n:>4}{h:>4}{rate:>8}{stake:>10,}{int(ret):>10,}{roi:>5.0f}%")

    for l in legs:
        lo, hi = spec.WINDOWS[l]
        row(_LEG_JA[l], f"{lo}〜{hi}", inw[l])
    row("芝+ダ", "", inw["shiba"] + inw["dirt"])
    row("全体", "", inw["shiba"] + inw["dirt"] + inw["shinba"])
    n_out = sum(len(v) for v in outw.values())
    if n_out:
        det = " / ".join(f"{_LEG_JA[l]}{len(outw[l])}点" for l in legs if outw[l])
        print(f"(窓外ピックは集計対象外・参考: {det})")
    print("\n-- 撤退基準判定 (事前登録 2026-07-02 / PRE_REGISTRATION_SUMMER2026.md) --")
    for strats, n_min, h_max, action, basis in spec.STOP_RULES:
        pk = [p for s in strats for p in inw[s]]
        n, h, *_ = summarize(pk)
        name = "+".join(_LEG_JA[s] for s in strats)
        if n >= n_min and h <= h_max:
            print(f"🚨 [{name}] 発動: n={n}≥{n_min} かつ 的中{h}≤{h_max} → {action} ({basis})")
        elif h > h_max:
            print(f"✅ [{name}] クリア: 的中{h}>{h_max} (n={n}) — この基準はもう発動しない")
        else:
            print(f"⏳ [{name}] 進行中: n={n}/{n_min} 的中{h} (n{n_min}到達時に的中≤{h_max}なら{action})")


def run_summary(strategy, evaluate):
    """evaluate=True(--season): 全開催日をその場で評価(未評価日もカバー)。
    False(--summary): キャッシュのみ集計し、未評価日を警告する。"""
    days = _season_days()
    if not days:
        print("シーズン期間外(6/1〜8/31)です")
        return
    today = datetime.date.today().strftime("%Y%m%d")
    picks_by_day, missing = {}, []
    for day in days:
        if evaluate:
            picks_by_day[day] = eval_day(day, strategy)
        else:
            cp = _cache_path(day, strategy)
            if os.path.exists(cp):
                try:
                    picks_by_day[day] = _read_cache(cp)
                except Exception:
                    missing.append(day)   # 仕様変更でキャッシュ無効
            elif day < today:
                missing.append(day)
    print(f"戦略={strategy} 期間={days[0]}〜{days[-1]}")
    for day, picks in sorted(picks_by_day.items()):
        if not picks:
            continue
        n, h, _, _, roi = summarize(picks)
        mark = "" if all(spec.in_window(p[1], day) for p in picks) else " (一部窓外)"
        print(f"  {day}: {n:>3}点 {h}的中 ROI{roi:.0f}%{mark}")
    if missing:
        we = [d for d in missing if datetime.datetime.strptime(d, "%Y%m%d").weekday() >= 5]
        print(f"⚠️ 未評価 {len(missing)}日 (土日{len(we)}日: {', '.join(we) or 'なし'})"
              f" — `--season` で評価すると解消します(平日は大半が非開催日)")
    summary_report(picks_by_day, strategy)


def _argval(args, flag):
    """--flag VALUE 形式の値を返す(無ければNone)。"""
    return args[args.index(flag) + 1] if flag in args else None


def _parse_band(s):
    lo, hi = s.split("-")
    return (float(lo), float(hi))


def _build_custom(args):
    """v2をベースに、渡された引数だけ上書きした即席戦略を返す: (name, Strat)。
    name は引数の署名を含むのでキャッシュが組合せごとに分かれる。"""
    shiba_band, dirt_band = (15.0, 80.0), (10.0, 80.0)
    shiba_blood, dirt_blood = set(SHIBA_BLOOD), set(US)
    min_career, pop_range = 2, None
    sig = []
    if (v := _argval(args, "--band-shiba")):
        shiba_band = _parse_band(v); sig.append(f"sb{v}")
    if (v := _argval(args, "--band-dirt")):
        dirt_band = _parse_band(v); sig.append(f"db{v}")
    if (v := _argval(args, "--blood-shiba")):
        shiba_blood = set(v.split(",")); sig.append("sl" + v.replace(",", "+"))
    if (v := _argval(args, "--blood-dirt")):
        dirt_blood = set(v.split(",")); sig.append("dl" + v.replace(",", "+"))
    if (v := _argval(args, "--career")):
        min_career = int(v); sig.append(f"c{v}")
    if (v := _argval(args, "--pop")):
        lo, hi = v.split("-"); pop_range = (int(lo), int(hi)); sig.append(f"p{v}")
    name = "custom_" + "_".join(sig)
    desc = (f"即席(v2ベース): 芝{shiba_band} {sorted(shiba_blood)} / "
            f"ダ{dirt_band} {sorted(dirt_blood)} / キャリア>={min_career} / 人気{pop_range or '無制限'}")
    return name, Strat(make_blood_filter(shiba_band, dirt_band, shiba_blood, dirt_blood,
                                         min_career=min_career, pop_range=pop_range), desc=desc)


_OVERRIDE_FLAGS = ("--band-shiba", "--band-dirt", "--blood-shiba", "--blood-dirt",
                   "--career", "--pop")


def main():
    args = sys.argv[1:]
    if "--list" in args:
        print("登録済みプリセット:")
        for name, spec in STRATEGIES.items():
            print(f"  {name:<10} {spec.desc}")
        return
    detail = "--detail" in args
    if any(f in args for f in _OVERRIDE_FLAGS):
        strategy, spec = _build_custom(args)
        STRATEGIES[strategy] = spec
        print(f"即席戦略: {spec.desc}")
    else:
        strategy = _argval(args, "--strategy") or "v2"
        if strategy not in STRATEGIES:
            print(f"未知の戦略 '{strategy}'。利用可能: {', '.join(STRATEGIES)} (--list で詳細)")
            return
    if "--season" in args or "--summary" in args:
        run_summary(strategy, evaluate="--season" in args)
        return
    days = [a for a in args if a.isdigit() and len(a) == 8] or [datetime.date.today().strftime("%Y%m%d")]
    allpicks = []
    print(f"戦略={strategy}")
    print(f"{'日':<10}{'点':>4}{'的中':>5}{'投資':>9}{'払戻':>10}{'収支':>10}{'ROI':>6}")
    for day in days:
        picks = eval_day(day, strategy)
        allpicks += picks
        n, h, stake, ret, roi = summarize(picks)
        print(f"{day:<10}{n:>4}{h:>5}{stake:>9,}{ret:>10,}{ret - stake:>+10,}{roi:>5.0f}%")
        if detail:
            for rid, strat, um, horse, od, win in picks:
                print(f"    {'○' if win else '×'} [{strat}] {um}番 {horse} {od}倍")
    if len(days) > 1:
        n, h, stake, ret, roi = summarize(allpicks)
        print(f"{'合計':<10}{n:>4}{h:>5}{stake:>9,}{ret:>10,}{ret - stake:>+10,}{roi:>5.0f}%")


if __name__ == "__main__":
    main()
