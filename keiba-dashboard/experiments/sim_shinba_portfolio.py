"""新種牡馬ポートフォリオの複利シミュレーション。
対象: エピファネイア牡(2019-)・エフフォーリア(2026-)・シスキン(2024-) の芝ダ新馬全頭。
賭け方: 単勝=残高1% + 複勝=残高2% (1点額は当日朝の残高で凍結・複利)。
※事後選択された種牡馬によるバックテスト=先読みバイアス込みの「上限値」。
実行: cd keiba-dashboard && python3 experiments/sim_shinba_portfolio.py
"""
import sqlite3
from collections import defaultdict

DB = "keiba.db"
START = 200_000   # 現行運用と同じ想定資金

con = sqlite3.connect(DB)
bets = []   # (date, sire_leg, horse, finish, win_payout, place_payout)
q = """
select date, sire, gender, horse, finish, win_payout, place_payout from entries
where class='新馬' and finish is not null and win_odds is not null and (
  (sire='エピファネイア' and gender='牡') or
  (sire='エフフォーリア') or
  (sire like '%シスキン%')
) order by date
"""
for date, sire, g, h, f, wp, pp in con.execute(q):
    leg = "エピ牡" if sire == "エピファネイア" else ("エフ" if sire == "エフフォーリア" else "シスキン")
    bets.append((date, leg, h, int(f), float(wp) if wp else 0.0, float(pp) if pp else 0.0))
con.close()
print(f"対象ベット: {len(bets)}頭  期間 {bets[0][0]} 〜 {bets[-1][0]}")
by_leg = defaultdict(int)
for b in bets: by_leg[b[1]] += 1
print("  内訳:", dict(by_leg))

def simulate(bets, f_win=0.01, f_place=0.02, skip_payout_top=0):
    """当日朝残高で1点額凍結・複利。skip_payout_top>0なら払戻上位N本を0円に(ストレス)。"""
    # ストレス: 単勝払戻の上位N本を特定
    win_returns = sorted([ (b[4], i) for i,b in enumerate(bets) if b[3]==1 and b[4]>0 ], reverse=True)
    killed = { i for _, i in win_returns[:skip_payout_top] }
    bank = START
    day_unit = {}
    peak = bank; maxdd = 0.0
    yearly_start = {}
    total_staked = 0.0
    for i, (date, leg, h, f, wp, pp) in enumerate(bets):
        if date not in day_unit:
            day_unit[date] = bank        # 朝凍結
        y = date[:4]
        if y not in yearly_start:
            yearly_start[y] = bank
        u = day_unit[date]
        sw = u * f_win; sp = u * f_place
        total_staked += sw + sp
        bank -= sw + sp
        if f == 1 and wp > 0 and i not in killed:
            bank += sw * wp / 100.0
        if f <= 3 and pp > 0:
            bank += sp * pp / 100.0
        peak = max(peak, bank)
        maxdd = max(maxdd, (peak - bank) / peak)
    return bank, maxdd, yearly_start, total_staked

def report(name, f_win, f_place, skip=0):
    bank, dd, ys, staked = simulate(bets, f_win, f_place, skip)
    yrs = sorted(ys)
    print(f"\n■ {name}")
    print(f"  最終残高 ¥{bank:,.0f} ({bank/START:.2f}倍)  最大DD {dd*100:.1f}%  総投入 ¥{staked:,.0f}")
    parts = []
    prev = None
    for y in yrs:
        if prev is not None:
            parts.append(f"{prev}:{ys[y]/ys[prev]:.2f}x")
        prev = y
    parts.append(f"{prev}:{bank/ys[prev]:.2f}x")
    print("  年別倍率:", " ".join(parts))

print("\n" + "="*70)
print("【本命】単勝1% + 複勝2%")
report("単1%+複2% (提案の形)", 0.01, 0.02)
report("単1%+複2%・単勝最高配当1本除外", 0.01, 0.02, skip=1)
report("単1%+複2%・単勝上位3本除外", 0.01, 0.02, skip=3)

print("\n" + "="*70)
print("【分解】どちらが運んでいるか")
report("単勝1%のみ", 0.01, 0.0)
report("複勝2%のみ", 0.0, 0.02)

print("\n" + "="*70)
print("【対照】脚を絞る")
bets_all = bets
bets = [b for b in bets_all if b[1] == "エフ"]
if bets: report("エフのみ 単1%+複2% (2026)", 0.01, 0.02)
bets = [b for b in bets_all if b[1] == "エピ牡"]
report("エピ牡のみ 単1%+複2% (2019-26)", 0.01, 0.02)
bets = [b for b in bets_all if b[1] == "シスキン"]
report("シスキンのみ 単1%+複2% (2024-26)", 0.01, 0.02)
bets = bets_all

print("\n" + "="*70)
print("【参考】フラット買いのROI (複利なし・100円/点)")
for leg in ("エピ牡", "エフ", "シスキン", None):
    sub = [b for b in bets if leg is None or b[1] == leg]
    n = len(sub)
    wret = sum(b[4] for b in sub if b[3] == 1)
    pret = sum(b[5] for b in sub if b[3] <= 3)
    label = leg or "3脚合算"
    print(f"  {label:<8} n={n:>4}  単勝ROI {wret/n:>6.1f}%  複勝ROI {pret/n:>6.1f}%")
