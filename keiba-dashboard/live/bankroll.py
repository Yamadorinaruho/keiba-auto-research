#!/usr/bin/env python3
"""【資金管理】夏戦略のステーキング(1点あたりの賭け額)を一元管理。

採用ルール(cc-memory decision 173/174):
  資金20万スタート / 1点あたり 残高の0.5% / 上限なし(当面) / 100円単位
  単位は日次更新(=その日の朝の残高で1点額を固定。同日中は据え置き)。
  ※上限はパリミュチュエルのオッズ自壊対策で本来2万が天井だが、残高400万未満では0.5%が
    2万に届かず上限が効かないため、当面 CAP=None(上限なし)。残高が大きくなったら再設定する。

state/bankroll.json に残高と当日の凍結ユニットを保存し、コミットで巡回間・日跨ぎに永続化。
夜の収支(summer_settle)が結果に応じて残高を更新する(同日二重精算はガード)。
"""
import os, json

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
PATH = os.path.join(STATE_DIR, "bankroll.json")

INIT = 200000      # 初期資金
FRAC = 0.005       # 1点あたり = 残高の0.5%
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


def unit_for(balance):
    """残高から1点額を算出: 0.5%・100円単位切り捨て・最低100円(CAPがあれば上限適用)。"""
    u = int(balance * FRAC) // 100 * 100
    if CAP is not None:
        u = min(CAP, u)
    return max(MIN_UNIT, u)


def daily_unit(date_iso, freeze=True):
    """当日の1点額を返す。当日未凍結なら現残高から算出して凍結(freeze=Trueのとき保存)。"""
    d = load()
    if d.get("unit_date") == date_iso and d.get("unit"):
        return d["unit"]
    u = unit_for(d["balance"])
    if freeze:
        d["unit_date"] = date_iso
        d["unit"] = u
        save(d)
    return u


def settle(date_iso, stake_total, ret_total, n, nhit):
    """その日の収支を残高に反映。戻り値 (state, applied)。同日は二重精算しない。"""
    d = load()
    if any(h.get("date") == date_iso for h in d.get("history", [])):
        return d, False
    before = d["balance"]
    d["balance"] = before - stake_total + ret_total
    d.setdefault("history", []).append(
        {"date": date_iso, "unit": d.get("unit"), "n": n, "hit": nhit,
         "stake": stake_total, "ret": ret_total, "before": before, "after": d["balance"]})
    save(d)
    return d, True
