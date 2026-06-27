"""【自動投票・実行層】即PAT(IPAT)を Playwright で操作し、単勝を購入する。

⚠️ セレクタ(SEL)はログイン後ページ依存で要・実機確認。まず DRY_RUN(購入確定の手前で停止+
   スクショ)で動かし、確認画面が正しく出ることを見てから本番(SUBMIT)に切り替える。

認証情報は環境変数(PC上の .env、Git管理外)から読む。チャット/リポジトリには絶対に置かない。
  IPAT_INET_ID / IPAT_KANYUSHA(加入者番号) / IPAT_PASSWORD(暗証番号) / IPAT_PARS(P-ARS番号)

使い方(PC上):
  set DRY_RUN=1 して  python -m live.auto_vote <date> --live   # まず確認画面まで
  問題なければ DRY_RUN=0 で本番。初回は AUTOVOTE_FORCE_AMOUNT=100 AUTOVOTE_MAX_RACES=1 推奨。
"""
import os, sys, datetime
from live import auto_vote

JST = datetime.timezone(datetime.timedelta(hours=9))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # keiba-dashboard/


def _load_env():
    """.env を読み込む(keiba-dashboard/.env を優先)。python-dotenv が無くても動く簡易パーサ。"""
    for envp in (os.path.join(_ROOT, ".env"), os.path.join(os.path.dirname(_ROOT), ".env")):
        if not os.path.exists(envp):
            continue
        for line in open(envp, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        return envp
    return None


_load_env()
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"   # 既定はドライラン(安全側)
SHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "ipat_shots")
LOGIN_URL = "https://www.ipat.jra.go.jp/"

# ── セレクタ。★=実ページで確認済 / ?=未確認(レース日に通常投票画面で確定する) ──
SEL = {
    # ★ step1: INET-ID入力ページ(実機確認済 2026-06-26)
    "inet_id":      "input[name='inetid']",            # ★ INET-ID(maxlen12)
    "login1_btn":   "a[onclick*='send']",              # ★ ログイン(画像リンク。onclick=send())
    # ★ step2: 加入者番号/暗証番号/P-ARS番号ページ(実機確認済 2026-06-27, /pw_080_i.cgi)
    "kanyusha":     "input[name='i']",                 # ★ 加入者番号(maxlen8)
    "password":     "input[name='p']",                 # ★ 暗証番号(maxlen4)
    "pars":         "input[name='r']",                 # ★ P-ARS番号(maxlen4)
    "login2_btn":   "a[onclick*='ToModernMenu']",      # ★ ログイン→新メニューUIへ
    # ★ 投票フロー(2026-06-27 マッピング。馬番/口数の"発売中の実挙動"は発売時間に最終確認)
    "menu_normal":  "button:has-text('通常')",          # ★ メニュー→通常投票(#!/bet/basic)
    "bet_type_sel": "#bet-basic-type",                  # ★ 式別select(option label='単勝')
    "umaban_fmt":   "#no{n}",                           # ★ 馬番チェックボックス(no1..no18)。発売前は非活性
    "unit_input":   "input[ng-model='vm.nUnit']",       # ★ 口数(1口=100円)
    "set_btn":      "button:text-is('セット')",          # ★ セット(完全一致。一括/予算/展開セットと区別)
    "confirm_btn":  "button:text-is('入力終了')",         # 入力終了(あれば)
    # ★ 購入予定リスト: 購入するで展開→一括セットで金額流込→合計金額入力→緑「購入する」確定(2026-06-27確認)
    "open_list_btn": "button:text-is('購入する')",        # ★ 購入予定リストを開く
    "bulk_amount":  "input[ng-model='vm.cAmount']",      # ★ 一括金額(百円単位。¥100→'1')
    "bulk_set_btn": "button:text-is('一括セット')",        # ★ 一括セット(全行に金額適用)
    "purchase_total": "input[ng-model='vm.cAmountTotal']",  # ★ 合計金額入力(確認欄)
    "submit_btn":   "button:text-is('購入する')",         # ★ 緑の購入する=最終確定(リスト内,.last)
}


def _venue_btn(venue, weekday):
    """開催場ボタンのセレクタ。例 venue=函館, weekday=土 → button:has-text('函館（土）')"""
    return f"button:has-text('{venue}（{weekday}）')"


def _need(name):
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"環境変数 {name} が未設定です(PCの.envに設定してください)")
    return v


def login(page):
    """即PATログイン。step1: INET-ID → step2: 加入者番号/暗証番号/P-ARS。"""
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.fill(SEL["inet_id"], _need("IPAT_INET_ID"))   # ★確認済
    page.click(SEL["login1_btn"])                       # ★ onclick=send()
    page.wait_for_load_state("networkidle")
    # step2(?要確認): 加入者番号ページ。セレクタが違う場合はここで例外→レース日に確定する
    page.fill(SEL["kanyusha"], _need("IPAT_KANYUSHA"))
    page.fill(SEL["password"], _need("IPAT_PASSWORD"))
    page.fill(SEL["pars"], _need("IPAT_PARS"))
    page.click(SEL["login2_btn"])
    page.wait_for_load_state("networkidle")


def bet_one_race(page, race_bets, weekday):
    """1レース分の単勝をカゴに入れる(race_bets=同一race_idの複数点)。
    weekday=開催曜日('土'/'日')。各点 amount(円)→口数(amount//100)。発売中のみ動作。"""
    r0 = race_bets[0]
    page.click(_venue_btn(r0["venue"], weekday), timeout=8000)   # 開催場
    page.wait_for_timeout(1200)
    page.click(f"button:has-text('{r0['rno']}R')", timeout=8000)  # レース
    page.wait_for_timeout(1500)
    page.select_option(SEL["bet_type_sel"], label="単勝")          # 式別=単勝
    page.wait_for_timeout(800)
    for b in race_bets:
        units = max(1, int(b["amount"]) // 100)                   # 1口=100円
        sel = SEL["umaban_fmt"].format(n=b["umaban"])             # 馬番=Angular。親labelをJSクリック
        page.eval_on_selector(sel, "el => el.closest('label').click()")
        page.wait_for_timeout(300)
        if not page.eval_on_selector(sel, "el => el.checked"):
            raise RuntimeError(f"馬番{b['umaban']}の選択に失敗(発売中か確認)")
        page.fill(SEL["unit_input"], str(units))
        page.click(SEL["set_btn"], timeout=6000)                  # 購入予想リストへ
        page.wait_for_timeout(800)
        print(f"  [set] {r0['venue']}{r0['rno']}R 単勝 {b['umaban']}番 {units}口(¥{units*100})")
    page.click(SEL["confirm_btn"], timeout=6000)                  # 入力終了(購入予定リストが自動展開)
    page.wait_for_timeout(1500)


def _purchase_race(p, race_bets, date, wd):
    """1レースを独立セッション(都度ログイン)で投票。実証済みの単レース経路を再利用。成立でTrue。"""
    r0 = race_bets[0]
    label = f"{r0['venue']}{r0['rno']}R"
    total = sum(b["amount"] for b in race_bets)
    unit = race_bets[0]["amount"]          # 全点同額(=日次1点額)。一括セットで全行に適用
    stamp = datetime.datetime.now(JST).strftime("%H%M%S")
    browser = p.chromium.launch(headless=False)
    page = browser.new_context().new_page()
    try:
        login(page)
        page.click(SEL["menu_normal"], timeout=10000)
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(2000)
        bet_one_race(page, race_bets, wd)                          # この1レースをステージ
        page.click(SEL["open_list_btn"], timeout=8000); page.wait_for_timeout(1500)
        ca = page.locator(SEL["bulk_amount"] + ":visible").first
        ca.click(); ca.fill(""); ca.press_sequentially(str(max(1, unit // 100)), delay=120); page.wait_for_timeout(300)
        page.click(SEL["bulk_set_btn"], timeout=6000); page.wait_for_timeout(1000)
        tt = page.locator(SEL["purchase_total"] + ":visible").first
        tt.click(); tt.fill(""); tt.press_sequentially(str(total), delay=110); page.keyboard.press("Tab"); page.wait_for_timeout(700)
        ready = not page.locator(SEL["submit_btn"]).last.is_disabled()
        page.screenshot(path=os.path.join(SHOT_DIR, f"confirm_{date}_{label}_{stamp}.png"), full_page=True)
        print(f"[ipat] {label} 購入予定リスト(¥{total:,}) 購入可={ready}")
        if not ready:
            print(f"[ipat] {label} 購入ボタン無効=中止"); return False
        if DRY_RUN or os.environ.get("CONFIRM_PURCHASE") != "1":
            print(f"[ipat] {label} 最終購入は保留(DRY)"); return False
        # ── ★実購入(取消不可)★ ──
        page.locator(SEL["submit_btn"]).last.click(); page.wait_for_timeout(1500)
        page.click("button:text-is('OK')", timeout=10000)         # 送信確認OK
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(SHOT_DIR, f"done_{date}_{label}_{stamp}.png"), full_page=True)
        body = page.evaluate("() => document.body.innerText")
        if any(k in body for k in ("正常に投票", "投票が完了", "受付", "投票完了")):
            for b in race_bets:
                auto_vote.record_bet(date, b["race_id"], b["umaban"], b["amount"], b.get("horse", ""))
            print(f"[ipat] ★{label} 投票完了(成立)★ {len(race_bets)}点 / ¥{total:,}")
            try:   # 買えたらSlack通知(.envにSLACK_WEBHOOK_URLが必要)
                from live import notify
                horses = " / ".join(f"{b['umaban']}番{b.get('horse','')}" for b in race_bets)
                notify.send(f"✅ *自動投票成立* {label} 単勝 {horses}  各¥{unit:,}(計¥{total:,})")
            except Exception:
                pass
            return True
        print(f"[ipat] ⚠️{label} 完了表示未確認=投票履歴で要確認(記録保留)")
        return False
    finally:
        browser.close()


def place_bets(plan, date):
    """プランを即PATで投票。1レース=1セッションで独立処理(1レース失敗しても他は継続)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("playwright未導入。pip install playwright && playwright install chromium")
    os.makedirs(SHOT_DIR, exist_ok=True)
    by_race = {}
    for b in plan:
        by_race.setdefault(b["race_id"], []).append(b)
    wd = {5: "土", 6: "日", 0: "月", 1: "火", 2: "水", 3: "木", 4: "金"}[datetime.date(int(date[:4]), int(date[4:6]), int(date[6:8])).weekday()]
    print(f"[ipat] {'DRY_RUN' if DRY_RUN else '★本番投票★'} {len(plan)}点 / {len(by_race)}レース (開催{wd})")
    with sync_playwright() as p:
        for rid, race_bets in by_race.items():
            try:
                _purchase_race(p, race_bets, date, wd)
            except Exception as e:
                r0 = race_bets[0]
                print(f"[ipat] {r0['venue']}{r0['rno']}R 失敗(スキップ): {str(e)[:90]}")


def check_creds():
    """値を表示せず、4項目が読めているかだけ確認する(✓=設定済み/✗=未設定)。"""
    envp = _load_env()
    print(f"[.env] {'読込: '+envp if envp else '見つからない(.envをkeiba-dashboard直下に置く)'}")
    ok = True
    for k in ("IPAT_INET_ID", "IPAT_KANYUSHA", "IPAT_PASSWORD", "IPAT_PARS"):
        v = os.environ.get(k)
        print(f"  {'✓' if v else '✗'} {k}" + (f" (長さ{len(v)})" if v else " 未設定"))
        ok = ok and bool(v)
    print("→ 4項目OK。次はDRY_RUNへ" if ok else "→ 未設定あり。.envを確認してください")
    return ok


if __name__ == "__main__":
    if "--check" in sys.argv:
        check_creds(); sys.exit(0)
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now(JST).strftime("%Y%m%d")
    plan = auto_vote.plan_bets(date, all_races="--all" in sys.argv)
    print(auto_vote.format_plan(plan))
    if plan and "--live" in sys.argv:
        place_bets(plan, date)
