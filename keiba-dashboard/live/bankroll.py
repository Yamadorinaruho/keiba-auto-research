#!/usr/bin/env python3
"""【資金管理】夏戦略のステーキング(1点あたりの賭け額)を一元管理。

採用ルール(cc-memory decision 173/174):
  資金20万スタート / 1点あたり 残高の0.5% / 上限なし(当面) / 100円単位
  単位は日次更新(=その日の朝の残高で1点額を固定。同日中は据え置き)。
  2026-07-05追記: 新馬第3戦略のみ1点=残高の1.0%(ユーザー決定。ケリー1/4≒1.0-1.2%が根拠)。
  芝・ダートは0.5%のまま。ステーキングは資金管理側の設定であり、凍結対象(買い目条件)ではない。
  ※上限はパリミュチュエルのオッズ自壊対策で本来2万が天井だが、残高400万未満では0.5%が
    2万に届かず上限が効かないため、当面 CAP=None(上限なし)。残高が大きくなったら再設定する。

state/bankroll.json に残高と当日の凍結ユニットを保存し、コミットで巡回間・日跨ぎに永続化。
夜の収支(summer_settle)が結果に応じて残高を更新する(同日二重精算はガード)。
"""
import os, json

from live import strategy_spec as _spec

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
PATH = os.path.join(STATE_DIR, "bankroll.json")

INIT = 200000      # 初期資金
FRAC = 0.005       # 1点あたり = 残高の0.5%(芝・ダート)
SHINBA_FRAC = 0.01 # 新馬のみ = 残高の1.0%(2026-07-05 ユーザー決定)
CAP = None         # 1点上限。当面なし(残高が大きくなったら数値を入れて再設定)
MIN_UNIT = 100     # 馬券最小単位


def load():
    if os.path.exists(PATH):
        with open(PATH) as f:
            return json.load(f)
    return {"balance": INIT, "unit_date": None, "unit": None, "history": []}


def save(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(PATH, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def unit_for(balance, frac=FRAC):
    """残高から1点額を算出: frac(既定0.5%)・100円単位切り捨て・最低100円(CAPがあれば上限適用)。"""
    u = int(balance * frac) // 100 * 100
    if CAP is not None:
        u = min(CAP, u)
    return max(MIN_UNIT, u)


def daily_unit(date_iso, freeze=True, strat=None):
    """当日の1点額を返す。strat="shinba"なら新馬用(残高1.0%)、それ以外は0.5%。
    当日未凍結なら現残高から両ユニットを算出して凍結(freeze=Trueのとき保存)。"""
    d = load()
    key = "unit_shinba" if strat == "shinba" else "unit"
    if d.get("unit_date") == date_iso and d.get(key):
        return d[key]
    if d.get("unit_date") != date_iso:
        d["unit"] = unit_for(d["balance"])
        d["unit_shinba"] = unit_for(d["balance"], SHINBA_FRAC)
        d["unit_date"] = date_iso
    else:   # 同日で片方だけ未設定(旧形式からの移行時)はそのキーだけ補完
        d[key] = unit_for(d["balance"], SHINBA_FRAC if strat == "shinba" else FRAC)
    if freeze:
        save(d)
    return d[key]


def settle(date_iso, stake_total, ret_total, n, nhit):
    """その日の収支を残高に反映。戻り値 (state, applied)。同日は二重精算しない。"""
    d = load()
    if any(h.get("date") == date_iso for h in d.get("history", [])):
        return d, False
    before = d["balance"]
    d["balance"] = before - stake_total + ret_total
    d.setdefault("history", []).append(
        {"date": date_iso, "unit": d.get("unit"), "unit_shinba": d.get("unit_shinba"), "n": n, "hit": nhit,
         "stake": stake_total, "ret": ret_total, "before": before, "after": d["balance"],
         "ver": _spec.SPEC_VERSION})
    save(d)
    return d, True
