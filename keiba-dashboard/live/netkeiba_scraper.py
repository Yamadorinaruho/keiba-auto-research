"""netkeiba スクレイパー
- レース日の race_id 一覧取得
- 出走表 (馬番・父母父・騎手・人気・オッズ・前走情報)
- 結果ページ (走破タイム・上り3F・着順・配当)
- 馬個別ページ (戦歴 → 前走系特徴量)

注意:
- レート制限 5秒/req 厳守
- User-Agent ブラウザ偽装
- ToS グレーゾーン → 個人利用前提、商用化や大量取得しない
"""
import os, sys, time, re, json
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}
RATE_LIMIT = 0.6  # 秒/req (高速化)
import threading
_rate_lock = threading.Lock()
_last_fetch = [0.0]

CACHE_DIR = ROOT / "state" / "netkeiba_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def fetch(url, cache_key=None, force=False):
    """レート制限付き fetch (グローバルロック)、キャッシュあり"""
    if cache_key:
        cache_file = CACHE_DIR / cache_key
        if cache_file.exists() and not force:
            return cache_file.read_text(encoding="utf-8")
    # グローバルレート制御 (スレッド間で共有)
    with _rate_lock:
        elapsed = time.time() - _last_fetch[0]
        if elapsed < RATE_LIMIT:
            time.sleep(RATE_LIMIT - elapsed)
        _last_fetch[0] = time.time()
    res = requests.get(url, headers=HEADERS, timeout=15)
    # netkeibaはサブドメインで文字コードが違う(db=EUC-JP / race=UTF-8)ため自動判定。
    res.encoding = res.apparent_encoding or res.encoding or "UTF-8"
    if cache_key:
        (CACHE_DIR / cache_key).write_text(res.text, encoding="utf-8")
    return res.text


def live_odds(race_id):
    """単勝の最新オッズをAJAXで取得(=ブラウザでリロードして見る値と同等)。
    返り値: (official_datetime, {馬番int: {'odds':float, 'pop':int}})。
    JRA公式更新時刻(official_datetime)も返るので、そのオッズの鮮度が正確に分かる。"""
    # action=update が無いと発走前のライブオッズが返らない(空)。type 1=単勝。
    raw = fetch(f"https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=1&action=update",
                cache_key=None, force=True)   # 常に最新を取得(キャッシュしない)
    try:
        j = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None, {}
    # status: "middle"=発走前ライブ / "result"=確定。両方とも有効なオッズ。
    # ("middle"を弾くと発走前の買い目判定でオッズ無し→芝/ダが買い目ゼロになる)
    if not isinstance(j, dict) or j.get("status") not in ("middle", "result"):
        return None, {}
    d = j.get("data", {})
    out = {}
    for k, v in d.get("odds", {}).get("1", {}).items():   # type "1" = 単勝
        try:
            odds, pop = float(v[0]), int(v[2])
        except (ValueError, IndexError, TypeError):
            continue
        if odds <= 0 or pop >= 9999:   # 取消・除外・発売前のセンチネル(例 ["-3.0","0.0","9999"])は除外
            continue
        out[int(k)] = {"odds": odds, "pop": pop}
    return d.get("official_datetime"), out


def get_race_ids_for_date(yyyymmdd):
    """指定日のレースID一覧
    - 未来/当日: race.netkeiba.com/top/race_list_sub.html (出走表ベース)
    - 過去:     db.netkeiba.com/race/list/ (結果ベース)
    両方試して取れる方を採用"""
    rids = set()
    # 1. 未来用 (race.netkeiba.com sub)
    url1 = f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={yyyymmdd}"
    try:
        html = fetch(url1, cache_key=f"race_list_sub_{yyyymmdd}.html")
        for m in re.finditer(r"race_id=(\d{12})", html):
            rids.add(m.group(1))
    except Exception:
        pass
    if rids:
        return sorted(rids)
    # 2. 過去用 (db.netkeiba.com)
    url2 = f"https://db.netkeiba.com/race/list/{yyyymmdd}/"
    try:
        html = fetch(url2, cache_key=f"race_list_{yyyymmdd}.html")
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select('a[href*="/race/"]'):
            href = a.get("href", "")
            m = re.search(r"/race/(\d{12})/?", href)
            if m:
                rids.add(m.group(1))
    except Exception:
        pass
    return sorted(rids)


def parse_shutuba(race_id):
    """出走表ページから出走馬リスト取得"""
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    html = fetch(url, cache_key=f"shutuba_{race_id}.html")
    soup = BeautifulSoup(html, "html.parser")

    # レース基本情報 (RaceData01: 距離/芝ダ/天気/馬場、RaceData02: クラス/頭数等)
    race_data1 = soup.select_one(".RaceData01")
    race_data2 = soup.select_one(".RaceData02")
    race_name_elem = soup.select_one(".RaceName")
    race_name = race_name_elem.get_text(strip=True) if race_name_elem else ""
    race_data1_text = race_data1.get_text(" ", strip=True) if race_data1 else ""
    race_data2_text = race_data2.get_text(" ", strip=True) if race_data2 else ""

    # 距離/芝ダ抽出。障害は「障芝2850m」等と表示され芝と誤判定されるため先に判定して除外(surface="障")。
    surface = "障" if "障" in race_data1_text else ("芝" if "芝" in race_data1_text else ("ダ" if "ダ" in race_data1_text else ""))
    dist_m = re.search(r"(\d{3,4})m", race_data1_text)
    distance = int(dist_m.group(1)) if dist_m else None

    # クラス: og:title meta タグ + race_data + race_name 統合
    og_title_meta = soup.select_one('meta[property="og:title"]')
    og_title = og_title_meta.get("content", "") if og_title_meta else ""
    class_str = ""
    all_text = og_title + " " + race_data1_text + " " + race_data2_text + " " + race_name
    # 重賞順 (G1 > G2 > G3 > L > オープン > 平場), 全角ローマ数字 GⅠ GⅡ GⅢ も検出
    for kw, norm in [("GⅠ","Ｇ１"), ("GⅡ","Ｇ２"), ("GⅢ","Ｇ３"),
                       ("Ｇ１","Ｇ１"), ("Ｇ２","Ｇ２"), ("Ｇ３","Ｇ３"),
                       ("G1","Ｇ１"), ("G2","Ｇ２"), ("G3","Ｇ３"),
                       ("(L)","OP(L)"), ("Ｌ)","OP(L)"), ("リステッド","OP(L)"),
                       ("オープン","ｵｰﾌﾟﾝ"), ("ｵｰﾌﾟﾝ","ｵｰﾌﾟﾝ"), ("OP","ｵｰﾌﾟﾝ"),
                       ("3勝","3勝"), ("2勝","2勝"), ("1勝","1勝"),
                       ("未勝利","未勝利"), ("新馬","新馬")]:
        if kw in all_text:
            class_str = norm; break

    # 出走馬テーブル
    horses = []
    rows = soup.select(".Shutuba_Table tr.HorseList")
    if not rows:
        rows = soup.select("table tr")
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 5: continue
        try:
            row = {}
            # 枠/馬番
            row["枠"] = tds[0].get_text(strip=True) if tds[0].get_text(strip=True).isdigit() else ""
            row["馬番"] = int(tds[1].get_text(strip=True)) if tds[1].get_text(strip=True).isdigit() else None
            # 馬名
            horse_link = tr.select_one(".HorseInfo a") or tr.select_one('a[href*="/horse/"]')
            row["馬名"] = horse_link.get_text(strip=True) if horse_link else ""
            row["馬URL"] = horse_link.get("href", "") if horse_link else ""
            row["馬ID"] = re.search(r"/horse/(\d+)", row["馬URL"]).group(1) if row["馬URL"] and re.search(r"/horse/(\d+)", row["馬URL"]) else ""
            # 性齢, 斤量, 騎手, 厩舎, 馬体重
            text_all = tr.get_text(" | ", strip=True)
            row["性齢"] = ""
            for t in text_all.split("|"):
                t = t.strip()
                if re.match(r"^[牡牝セ]\d+$", t):
                    row["性齢"] = t; break
            # 騎手
            jockey = tr.select_one('a[href*="/jockey/"]')
            row["騎手"] = jockey.get_text(strip=True) if jockey else ""
            # 厩舎
            trainer = tr.select_one('a[href*="/trainer/"]')
            row["調教師"] = trainer.get_text(strip=True) if trainer else ""
            # 単勝オッズ・人気
            odds_elems = tr.select(".Popular, .Odds")
            for elem in odds_elems:
                t = elem.get_text(strip=True)
                if re.match(r"^\d+\.\d+$", t):
                    row["単勝オッズ"] = float(t)
                elif re.match(r"^\d+$", t):
                    row.setdefault("人気", int(t))
            if row["馬番"]:
                horses.append(row)
        except Exception as e:
            continue

    return {"race_id": race_id, "race_name": race_name,
            "surface": surface, "distance": distance, "class": class_str,
            "horses": horses}


def parse_result(race_id):
    """結果ページから走破タイム・上り3F・着順・配当"""
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    html = fetch(url, cache_key=f"result_{race_id}.html")
    soup = BeautifulSoup(html, "html.parser")

    horses = []
    for tr in soup.select(".RaceTable01 tr, .ResultTable01 tr"):
        tds = tr.find_all("td")
        if len(tds) < 5: continue
        try:
            row = {}
            cells = [td.get_text(strip=True) for td in tds]
            # 着順
            try: row["着順"] = int(cells[0])
            except: continue
            # 馬番 (cells = [着順, 枠, 馬番, 馬名, ...])
            try:
                umaban = int(cells[2])
                if 1 <= umaban <= 18:
                    row["馬番"] = umaban
            except (ValueError, IndexError):
                pass
            # 走破タイム (m:ss.s形式)
            for c in cells:
                m = re.match(r"^(\d):(\d{2})\.(\d)$", c)
                if m:
                    row["走破タイム"] = int(m.group(1))*60 + int(m.group(2)) + int(m.group(3))*0.1
                    break
            # 上り3F
            for c in cells:
                m = re.match(r"^(\d{2})\.(\d)$", c)
                if m and 30 <= float(c) <= 50:
                    row["上り3F"] = float(c); break
            # コーナー通過順 (例: "5-3-2-2")
            for c in cells:
                if re.match(r"^\d{1,2}(-\d{1,2}){1,3}$", c):
                    parts = c.split("-")
                    row["前4角"] = int(parts[-1])
                    break
            horses.append(row)
        except: continue

    # 配当 (複勝)
    place_payouts = {}
    for tr in soup.select(".Payout_Detail_Table tr"):
        ths = tr.find_all("th")
        if not ths or "複勝" not in ths[0].get_text(): continue
        # 馬番と配当のペア
        nums_td = tr.select_one(".Result")
        pays_td = tr.select_one(".Payout")
        if nums_td and pays_td:
            nums = re.findall(r"\d+", nums_td.get_text())
            pays = re.findall(r"\d+", pays_td.get_text().replace(",", ""))
            for n, p in zip(nums, pays):
                place_payouts[int(n)] = float(p)

    return {"race_id": race_id, "horses": horses, "place_payouts": place_payouts}


def parse_horse(horse_id, max_history=10):
    """馬個別ページから 父・母父・戦歴を取得
    - /horse/result/{id}/ : 戦歴
    - /horse/ped/{id}/    : 父・母父
    """
    # === 戦歴 ===
    url_result = f"https://db.netkeiba.com/horse/result/{horse_id}/"
    html = fetch(url_result, cache_key=f"hresult_{horse_id}.html")
    soup = BeautifulSoup(html, "html.parser")
    history = []
    table = soup.select_one(".db_h_race_results")
    if table:
        # ヘッダから列インデックスを取得
        headers = [th.get_text(strip=True) for th in table.select("thead th")]
        idx = {h: i for i, h in enumerate(headers)}
        for tr in table.select("tbody tr")[:max_history]:
            tds = [td.get_text(strip=True) for td in tr.select("td")]
            if len(tds) < 20: continue
            try:
                row = {}
                row["date"] = tds[idx.get("日付", 0)]
                row["finish"] = int(tds[idx["着順"]]) if "着順" in idx and tds[idx["着順"]].isdigit() else None
                # 距離 (例 "ダ1200" or "芝2000")
                if "距離" in idx:
                    d = tds[idx["距離"]]
                    m = re.match(r"^(芝|ダ|障)(\d{3,4})$", d)
                    if m:
                        row["surface"] = "芝" if m.group(1) == "芝" else "ダ"
                        row["distance"] = int(m.group(2))
                # タイム (m:ss.s)
                if "タイム" in idx:
                    t = tds[idx["タイム"]]
                    m = re.match(r"^(\d):(\d{2})\.(\d)$", t)
                    if m:
                        row["finish_time"] = int(m.group(1))*60 + int(m.group(2)) + int(m.group(3))*0.1
                # 着差
                if "着差" in idx:
                    s = tds[idx["着差"]]
                    try: row["margin"] = float(s)
                    except: pass
                # 通過 (例 "14-13" → 4角=13)
                if "通過" in idx:
                    p = tds[idx["通過"]]
                    if "-" in p:
                        try: row["corner4"] = int(p.split("-")[-1])
                        except: pass
                # 上り (例 "35.7")
                if "上り" in idx:
                    a = tds[idx["上り"]]
                    try:
                        v = float(a)
                        if 30 <= v <= 50: row["last_3f"] = v
                    except: pass
                # クラス推測 (race_name から)
                if "レース名" in idx:
                    rn = tds[idx["レース名"]]
                    row["race_name"] = rn
                    for kw in ["G1","G2","G3","Ｇ１","Ｇ２","Ｇ３","オープン","ｵｰﾌﾟﾝ","OP(L)","リステッド","3勝","2勝","1勝","未勝利","新馬"]:
                        if kw in rn:
                            row["class"] = kw; break
                history.append(row)
            except Exception:
                continue

    # === 父・母父 (/horse/ped/{id}/ blood_table) ===
    # 5世代血統表: 父=cells[0] rs=16 b_ml, 母=最初の rs=16 b_fml セル, 母父=その直後のb_mlセル
    sire = ""; broodmare_sire = ""
    url_ped = f"https://db.netkeiba.com/horse/ped/{horse_id}/"
    html_ped = fetch(url_ped, cache_key=f"hped_{horse_id}.html")
    soup_ped = BeautifulSoup(html_ped, "html.parser")
    blood = soup_ped.select_one(".blood_table")
    if blood:
        cells = blood.select("td")
        def first_word(td):
            """最初のリンクテキストから「馬名」だけ抽出 (年・色・[血統]タグ 除外)"""
            a = td.find("a")
            if a:
                txt = a.get_text(strip=True)
                # 年・余分な情報を取り除き
                m = re.match(r"^([^\d\(]+)", txt)
                if m: return m.group(1).strip()
                return txt
            return ""

        # 父: 最初のセル (rs=16, b_ml)
        for td in cells:
            if "b_ml" in (td.get("class") or []) and td.get("rowspan") == "16":
                sire = first_word(td); break

        # 母父: 母 (rs=16 b_fml) の直後の rs=8 b_ml
        for i, td in enumerate(cells):
            if "b_fml" in (td.get("class") or []) and td.get("rowspan") == "16":
                # 次セルが 母父
                if i+1 < len(cells):
                    nxt = cells[i+1]
                    if "b_ml" in (nxt.get("class") or []):
                        broodmare_sire = first_word(nxt)
                break

    # === 生年月日 (/horse/{id}/ プロフィール) ===
    birth_date = ""
    url_profile = f"https://db.netkeiba.com/horse/{horse_id}/"
    try:
        html_p = fetch(url_profile, cache_key=f"hprofile_{horse_id}.html")
        soup_p = BeautifulSoup(html_p, "html.parser")
        # プロフィールテーブル: <table class="db_prof_table">; "生年月日" の行
        for tr in soup_p.select(".db_prof_table tr"):
            th = tr.select_one("th")
            td = tr.select_one("td")
            if th and td and "生年月日" in th.get_text():
                # 例: "2022年4月30日"
                m = re.match(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", td.get_text(strip=True))
                if m:
                    birth_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                break
    except Exception:
        pass

    return {"horse_id": horse_id, "sire": sire, "broodmare_sire": broodmare_sire,
            "birth_date": birth_date, "history": history}


def parse_odds(race_id):
    """JRA-VAN AJAX endpoint で 単勝オッズ・複勝オッズ・人気 を取得"""
    url = f"https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=1"
    html = fetch(url, cache_key=f"odds_api_{race_id}.json")
    try:
        data = json.loads(html)
    except json.JSONDecodeError:
        return {}
    if data.get("status") != "result": return {}
    odds_block = data.get("data", {}).get("odds", {})
    out = {}
    # 単勝
    for num_str, vals in odds_block.get("1", {}).items():
        try:
            num = int(num_str)
            out.setdefault(num, {})["win_odds"] = float(vals[0])
            out[num]["popularity"] = int(vals[2])
        except (ValueError, IndexError):
            continue
    # 複勝(min/max)
    for num_str, vals in odds_block.get("2", {}).items():
        try:
            num = int(num_str)
            out.setdefault(num, {})["place_odds_min"] = float(vals[0])
            out[num]["place_odds_max"] = float(vals[1])
        except (ValueError, IndexError):
            continue
    return out


def get_full_race_data(race_id):
    """1レース完全データ: 出走表 + 馬個別 (父母父+戦歴) + オッズ"""
    print(f"[1/3] 出走表 {race_id}", flush=True)
    shutuba = parse_shutuba(race_id)
    print(f"[2/3] オッズ {race_id}", flush=True)
    odds = parse_odds(race_id)
    print(f"[3/3] 馬個別 ({len(shutuba['horses'])}頭)", flush=True)
    for h in shutuba["horses"]:
        if h.get("馬ID"):
            hd = parse_horse(h["馬ID"])
            h["父"] = hd["sire"]
            h["母父"] = hd["broodmare_sire"]
            h["戦歴"] = hd["history"]
            # 前走情報を取り出し
            if hd["history"]:
                prev = hd["history"][0]
                h["前走着順"] = prev.get("finish")
                h["前走距離"] = prev.get("distance")
                h["前走走破タイム"] = prev.get("finish_time")
                h["前走上り3F"] = prev.get("last_3f")
                h["前走4角"] = prev.get("corner4")
                h["前走着差"] = prev.get("margin")
        if h.get("馬番") in odds:
            h.setdefault("単勝オッズ", odds[h["馬番"]].get("win_odds"))
            h.setdefault("人気", odds[h["馬番"]].get("popularity"))
    return shutuba


if __name__ == "__main__":
    # テスト: 直近の開催日で動作確認
    if len(sys.argv) < 2:
        print("Usage: python netkeiba_scraper.py YYYYMMDD")
        print("       python netkeiba_scraper.py race RACE_ID")
        sys.exit(1)
    if sys.argv[1] == "race":
        rid = sys.argv[2]
        d = get_full_race_data(rid)
        print(f"\n=== {d['race_name']} ({d['surface']}{d['distance']}m, {d['class']}) ===")
        for h in d["horses"][:5]:
            print(f"  {h.get('馬番')} {h.get('馬名')} ({h.get('性齢')}) 父:{h.get('父','?')} 母父:{h.get('母父','?')}")
            print(f"     {h.get('単勝オッズ','?')}倍 {h.get('人気','?')}人気 / 前走: 着{h.get('前走着順','?')} {h.get('前走距離','?')}m タイム{h.get('前走走破タイム','?')} 上3F{h.get('前走上り3F','?')} 4角{h.get('前走4角','?')} 着差{h.get('前走着差','?')}")
    else:
        date_ = sys.argv[1]
        print(f"レースID取得 ({date_})...", flush=True)
        rids = get_race_ids_for_date(date_)
        print(f"  {len(rids)} レース")
        for rid in rids[:5]:
            print(f"    {rid}")
