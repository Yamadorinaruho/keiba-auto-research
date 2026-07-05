#!/usr/bin/env python3
"""【仕様の単一情報源】夏3戦略の買い目条件・稼働窓をここに一元定義する。

live側(summer_notify/summer_dirt/summer_shinba/summer_schedule)と
評価側(strat_eval)は必ずこのモジュールを参照する。帯・血統・キャリア・窓を
変更するときはここだけを書き換え、decisionに記録すること(二重定義は
「decision 10-50 vs 実装10-80」型のスペックドリフトを生むため禁止)。

現行仕様 v2 (血統フィルタ, decision 182 とその後の更新):
  芝    : 全会場 × 芝 未勝利 × 3歳牝 × 単勝15-80倍 × 3走目以上 × 父{ディープ/サンデー他/カナロア}
  ダート: 全会場 × ダ≤1400m × 未勝利〜OP × 3歳牝 × 単勝10-80倍 × 3走目以上 × 父 米国系
          (decision 182時点は牝全年齢×10-50。その後 3歳牝限定に伴い上限を80へ拡大)
  新馬  : 全会場 × 芝 2歳新馬 × 父{エピファネイア, エフフォーリア} 全頭・オッズ不問
"""
import hashlib
import json

SPEC_VERSION = "v2"

# ── 芝戦略 ──
SHIBA_BAND = (15.0, 80.0)                 # 単勝オッズ lo <= od < hi
GOOD2 = {"ディープ系"}                     # v1 score互換の血統加点 +2
GOOD1 = {"サンデー系他", "カナロア系"}      # +1
SHIBA_BLOOD = GOOD2 | GOOD1

# ── ダート戦略 ──
DIRT_BAND = (10.0, 80.0)
DIRT_BLOOD = {"米国系"}
DIRT_CLS = {"未勝利", "1勝", "500万", "2勝", "1000万", "3勝", "1600万", "オープン", "OP(L)"}
DIRT_MAX_DIST = 1400

# ── 共通 ──
MIN_CAREER = 2        # 過去出走2戦以上 = 3走目以上

# ── 新馬戦略 ──
SHINBA_SIRES = {"エピファネイア", "エフフォーリア"}

# ── 稼働窓 (MM-DD, 両端含む)。窓外のピックは「参考」であり成績評価に含めない ──
WINDOWS = {
    "shiba":  ("06-16", "08-31"),
    "dirt":   ("06-16", "08-31"),
    "shinba": ("06-01", "08-31"),
}
SEASON_START = min(w[0] for w in WINDOWS.values())   # "06-01"
SEASON_END = max(w[1] for w in WINDOWS.values())     # "08-31"


# ── 撤退基準 (事前登録 2026-07-02, 詳細は PRE_REGISTRATION_SUMMER2026.md) ──
# 判定対象は「稼働窓内のフォワード実測」のみ。的中数ベース(オッズ非依存で検出力が明確)。
# (対象戦略, 窓内n下限, 的中数上限, アクション, 根拠) : n到達時点で的中数が上限以下なら発動。
STOP_RULES = [
    (("shiba", "dirt"), 100, 0, "停止",       "的中率6%仮定でP(0/100)≈0.2%"),
    (("shiba", "dirt"), 200, 4, "ユニット半減", "的中率6%仮定でP(≤4/200)≈0.8%"),
    (("shinba",),        60, 5, "停止",       "的中率19.7%仮定でP(≤5/60)≈2%"),
]


def in_window(strat, day):
    """day="YYYYMMDD" が戦略stratの稼働窓内か。"""
    md = f"{day[4:6]}-{day[6:8]}"
    lo, hi = WINDOWS[strat]
    return lo <= md <= hi


def band_str(band):
    lo, hi = band
    return f"{lo:g}-{hi:g}倍"


def fingerprint():
    """仕様のフィンガープリント(8桁)。キャッシュ・ログに埋めて、
    仕様変更後に古い評価結果を黙って混ぜてしまう事故を検出する。"""
    payload = {
        "ver": SPEC_VERSION,
        "shiba": [SHIBA_BAND, sorted(SHIBA_BLOOD)],
        "dirt": [DIRT_BAND, sorted(DIRT_BLOOD), sorted(DIRT_CLS), DIRT_MAX_DIST],
        "career": MIN_CAREER,
        "shinba": sorted(SHINBA_SIRES),
        "windows": WINDOWS,
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                   default=list).encode()).hexdigest()[:8]
