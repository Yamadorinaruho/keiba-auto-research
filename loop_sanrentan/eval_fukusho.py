"""
n45モデルの複勝評価スクリプト
TOP1/TOP2/TOP3それぞれ1点買い、TOP1-3の3点買い
"""
import os, sys, math, json, time
import numpy as np
import pandas as pd
import lightgbm as lgb

# ---------------------------------------------------------------------------
# Config: n45 = noodds + nl=200, lr=0.01, n39ベース + 生後日数
# ---------------------------------------------------------------------------
LGBM_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "learning_rate": 0.01,
    "num_leaves": 200,
    "max_depth": -1,
    "min_child_samples": 20,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "verbose": -1,
    "n_jobs": -1,
    "seed": 42,
}
NUM_BOOST_ROUND = 500
EARLY_STOPPING_ROUNDS = 50

# Race filters (same as n45)
MIN_RACE_RUNNERS = 12
MIN_TOP1_ODDS = 3.0  # 1番人気の最低オッズ

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Data_2020_fixed.csv")
TRAIN_END = "2023.12.31"
TEST_START = "2024. 1. 1"

# ---------------------------------------------------------------------------
# Data loading (same as prepare.py)
# ---------------------------------------------------------------------------
def load_data():
    df = pd.read_csv(DATA_PATH, encoding="utf-8", low_memory=False).copy()
    date_str = df["日付(yyyy.mm.dd)"].str.replace(r"\s+", "", regex=True)
    df["date"] = pd.to_datetime(date_str, format="%Y.%m.%d")
    df["race_id"] = (
        df["date"].dt.strftime("%Y%m%d")
        + "_" + df["場所"].astype(str)
        + "_R" + df["R"].astype(str)
    )
    df["finish"] = pd.to_numeric(df["確定着順"], errors="coerce")
    df["odds"] = pd.to_numeric(df["単勝オッズ"], errors="coerce")
    df["umaban"] = pd.to_numeric(df["馬番"], errors="coerce")
    # 複勝配当
    df["fukusho_payout"] = pd.to_numeric(df["複勝配当"], errors="coerce").fillna(0)
    df = df[df["finish"] > 0].copy()
    df = df.dropna(subset=["odds"]).copy()
    df = df.sample(frac=1, random_state=42).sort_values(["date", "race_id"]).reset_index(drop=True)
    return df

# ---------------------------------------------------------------------------
# Feature engineering (n45 = n01 base + 年齢×キャリア + 生後日数, no odds)
# ---------------------------------------------------------------------------
def engineer_features(df):
    df = df.copy()
    # Categoricals
    for col, code_name in [("場所","場所_code"),("性別","性別_code"),("芝・ダ","芝ダ_code"),
                            ("馬場状態","馬場_code"),("天気","天気_code"),("コース区分","コース区分_code"),
                            ("所属","所属_code")]:
        if col in df.columns:
            df[code_name] = df[col].astype("category").cat.codes

    if "枠番" in df.columns and "馬番" in df.columns:
        df["枠馬番差"] = df["馬番"] - df["枠番"]
    if "馬番" in df.columns and "頭数" in df.columns:
        df["馬番比率"] = df["馬番"] / df["頭数"].clip(lower=1)
    if "斤量" in df.columns and "馬齢斤量差" in df.columns:
        df["斤量_num"] = pd.to_numeric(df["斤量"], errors="coerce")
        df["馬齢斤量差_num"] = pd.to_numeric(df["馬齢斤量差"], errors="coerce")
        df["斤量_adjusted"] = df["斤量_num"] + df["馬齢斤量差_num"]
    if "前走着順" in df.columns:
        df["前走着順_num"] = pd.to_numeric(df["前走着順"], errors="coerce")
        df["前走top3"] = (df["前走着順_num"] <= 3).astype(float)
    if "前走単勝オッズ" in df.columns:
        df["前走log_odds"] = np.log1p(pd.to_numeric(df["前走単勝オッズ"], errors="coerce"))
    if "前走着順" in df.columns and "前走人気" in df.columns:
        prev_finish = pd.to_numeric(df["前走着順"], errors="coerce")
        prev_ninki = pd.to_numeric(df["前走人気"], errors="coerce")
        df["前走着順_人気差"] = prev_finish - prev_ninki

    # n39: 年齢×キャリア
    if "年齢" in df.columns and "キャリア" in df.columns:
        age = pd.to_numeric(df["年齢"], errors="coerce")
        career = pd.to_numeric(df["キャリア"], errors="coerce")
        df["年齢xキャリア"] = age * career

    # n45: +生後日数
    if "生後日数" in df.columns:
        df["生後日数_num"] = pd.to_numeric(df["生後日数"], errors="coerce")

    return df

# ---------------------------------------------------------------------------
# n45 feature columns (no odds!)
# ---------------------------------------------------------------------------
def get_n45_features(df):
    base = [
        # NO 単勝オッズ (noodds)
        "前走着順_num", "前走top3",
        "前走log_odds", "前走着順_人気差",
        "場所_code", "芝ダ_code", "馬場_code",
        "年齢", "性別_code", "斤量_num",
        "頭数", "馬番比率",
        "間隔_num", "走前",
        "枠番", "距離",
        # n39
        "年齢xキャリア",
        # n45
        "生後日数_num",
    ]
    return [c for c in base if c in df.columns]

# ---------------------------------------------------------------------------
# Fukusho evaluation
# ---------------------------------------------------------------------------
def evaluate_fukusho(df_test, predictions, strategy="top1"):
    """
    strategy: "top1", "top2", "top3", "top123"
    複勝: 3着以内に入れば複勝配当を回収
    """
    df_temp = df_test[["race_id", "date", "umaban", "odds", "finish", "fukusho_payout"]].copy()
    df_temp["pred"] = predictions

    total_wagered = 0
    total_return = 0
    hits = 0
    total_bets = 0
    race_results = []  # (race_id, date, wagered, returned)

    for race_id, group in df_temp.groupby("race_id"):
        if len(group) < MIN_RACE_RUNNERS:
            continue
        min_odds_in_race = group["odds"].min()
        if min_odds_in_race < MIN_TOP1_ODDS:
            continue

        # Shuffle to prevent tie-breaking leak
        group = group.sample(frac=1, random_state=42)
        ranked = group.nlargest(3, "pred")
        horses = ranked.iloc  # top1=0, top2=1, top3=2

        if strategy == "top1":
            targets = [0]
        elif strategy == "top2":
            targets = [1]
        elif strategy == "top3":
            targets = [2]
        elif strategy == "top123":
            targets = [0, 1, 2]
        else:
            targets = [0]

        wagered = len(targets) * 100
        returned = 0
        race_hits = 0

        for idx in targets:
            if idx >= len(ranked):
                continue
            horse = horses[idx]
            total_bets += 1
            # 複勝的中: 3着以内
            if horse["finish"] <= 3 and horse["fukusho_payout"] > 0:
                returned += horse["fukusho_payout"]
                race_hits += 1

        total_wagered += wagered
        total_return += returned
        hits += race_hits
        race_results.append((race_id, group["date"].iloc[0], wagered, returned))

    if total_wagered == 0:
        return {}

    roi = total_return / total_wagered * 100
    hit_rate = hits / total_bets * 100 if total_bets > 0 else 0
    profit = total_return - total_wagered

    # Dates
    dates = [d for _, d, _, _ in race_results]
    date_range_days = (max(dates) - min(dates)).days + 1
    years = max(date_range_days / 365.25, 0.1)
    annual_bets = len(race_results) / years

    # Monthly Sharpe
    rr_df = pd.DataFrame(race_results, columns=["race_id", "date", "wagered", "returned"])
    rr_df["year_month"] = rr_df["date"].dt.to_period("M")
    monthly = rr_df.groupby("year_month").agg(
        wagered=("wagered", "sum"),
        returned=("returned", "sum"),
    )
    monthly["roi"] = monthly["returned"] / monthly["wagered"] * 100
    monthly_returns = monthly["roi"].values - 100
    if len(monthly_returns) > 1 and np.std(monthly_returns) > 0:
        sharpe = np.mean(monthly_returns) / np.std(monthly_returns) * math.sqrt(12)
    else:
        sharpe = 0.0

    monthly_roi_dict = {str(k): round(v, 2) for k, v in monthly["roi"].items()}

    return {
        "strategy": strategy,
        "roi": round(roi, 2),
        "hit_rate": round(hit_rate, 2),
        "total_bets_races": len(race_results),
        "total_bets_points": total_bets,
        "annual_bets": round(annual_bets, 1),
        "sharpe": round(sharpe, 3),
        "total_return": int(total_return),
        "total_wagered": total_wagered,
        "profit": profit,
        "monthly_roi": monthly_roi_dict,
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def evaluate_fukusho_v2(df_test, predictions, strategy="top1",
                         min_runners=0, max_top1_odds=999, min_top1_odds=0,
                         min_fukusho_odds=0, confidence_top_n=0):
    """
    改良版複勝評価
    min_runners: 最低出走頭数 (0=制限なし)
    max_top1_odds: 1番人気オッズ上限 (堅いレース狙い)
    min_top1_odds: 1番人気オッズ下限
    min_fukusho_odds: 複勝オッズ下限フィルタ
    confidence_top_n: モデルの上位N頭のスコアが閾値以上の場合のみ (0=制限なし)
    """
    df_temp = df_test[["race_id", "date", "umaban", "odds", "finish",
                        "fukusho_payout", "fukusho_odds_lower"]].copy()
    df_temp["pred"] = predictions

    total_wagered = 0
    total_return = 0
    hits = 0
    total_bets = 0
    race_results = []

    for race_id, group in df_temp.groupby("race_id"):
        n_runners = len(group)
        if min_runners > 0 and n_runners < min_runners:
            continue
        min_odds_in_race = group["odds"].min()
        if min_odds_in_race < min_top1_odds:
            continue
        if min_odds_in_race > max_top1_odds:
            continue

        group = group.sample(frac=1, random_state=42)
        ranked = group.nlargest(3, "pred")
        horses = ranked.iloc

        if strategy == "top1":
            targets = [0]
        elif strategy == "top2":
            targets = [1]
        elif strategy == "top3":
            targets = [2]
        elif strategy == "top123":
            targets = [0, 1, 2]
        elif strategy == "top1_filtered":
            # TOP1のみ、複勝オッズ下限フィルタ付き
            if len(ranked) > 0 and ranked.iloc[0]["fukusho_odds_lower"] >= min_fukusho_odds:
                targets = [0]
            else:
                continue
        else:
            targets = [0]

        wagered = len(targets) * 100
        returned = 0
        race_hits = 0

        for idx in targets:
            if idx >= len(ranked):
                continue
            horse = horses[idx]
            total_bets += 1
            if horse["finish"] <= 3 and horse["fukusho_payout"] > 0:
                returned += horse["fukusho_payout"]
                race_hits += 1

        total_wagered += wagered
        total_return += returned
        hits += race_hits
        race_results.append((race_id, group["date"].iloc[0], wagered, returned))

    if total_wagered == 0:
        return None

    roi = total_return / total_wagered * 100
    hit_rate = hits / total_bets * 100 if total_bets > 0 else 0
    profit = total_return - total_wagered

    dates = [d for _, d, _, _ in race_results]
    date_range_days = (max(dates) - min(dates)).days + 1
    years = max(date_range_days / 365.25, 0.1)
    annual_bets = len(race_results) / years

    rr_df = pd.DataFrame(race_results, columns=["race_id", "date", "wagered", "returned"])
    rr_df["year_month"] = rr_df["date"].dt.to_period("M")
    monthly = rr_df.groupby("year_month").agg(wagered=("wagered","sum"), returned=("returned","sum"))
    monthly["roi"] = monthly["returned"] / monthly["wagered"] * 100
    monthly_returns = monthly["roi"].values - 100
    if len(monthly_returns) > 1 and np.std(monthly_returns) > 0:
        sharpe = np.mean(monthly_returns) / np.std(monthly_returns) * math.sqrt(12)
    else:
        sharpe = 0.0
    monthly_roi_dict = {str(k): round(v, 2) for k, v in monthly["roi"].items()}

    return {
        "roi": round(roi, 2),
        "hit_rate": round(hit_rate, 2),
        "total_bets_races": len(race_results),
        "total_bets_points": total_bets,
        "annual_bets": round(annual_bets, 1),
        "sharpe": round(sharpe, 3),
        "total_return": int(total_return),
        "total_wagered": total_wagered,
        "profit": profit,
        "monthly_roi": monthly_roi_dict,
    }


def main():
    t0 = time.time()
    print("=" * 60)
    print("n45モデル 複勝評価 (改良版)")
    print("=" * 60)

    print("\n[1/4] Loading data...")
    df = load_data()
    print(f"  Records: {len(df):,}")

    has_fukusho = (df["fukusho_payout"] > 0).sum()
    print(f"  Records with fukusho payout: {has_fukusho:,} ({has_fukusho/len(df)*100:.1f}%)")

    # 複勝オッズ下限も読み込む
    df["fukusho_odds_lower"] = pd.to_numeric(df["複勝オッズ下限"], errors="coerce").fillna(0)

    print("\n[2/4] Engineering features (n45)...")
    df = engineer_features(df)

    train_end = pd.to_datetime("2023.12.31", format="%Y.%m.%d")
    test_start = pd.to_datetime("2024.1.1", format="%Y.%m.%d")
    train = df[df["date"] <= train_end].copy()
    test = df[df["date"] >= test_start].copy()
    print(f"  Train: {len(train):,}  Test: {len(test):,}")

    feature_cols = get_n45_features(df)
    print(f"  Features ({len(feature_cols)}): {feature_cols}")

    train["target"] = (train["finish"] <= 3).astype(int)
    test["target"] = (test["finish"] <= 3).astype(int)

    X_train = train[feature_cols].values
    y_train = train["target"].values
    X_test = test[feature_cols].values

    val_size = int(len(X_train) * 0.2)
    X_tr, y_tr = X_train[:-val_size], y_train[:-val_size]
    X_val, y_val = X_train[-val_size:], y_train[-val_size:]

    print("\n[3/4] Training LightGBM...")
    dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=feature_cols, free_raw_data=False)
    dval = lgb.Dataset(X_val, label=y_val, feature_name=feature_cols, free_raw_data=False)
    model = lgb.train(
        LGBM_PARAMS, dtrain, num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[dtrain, dval], valid_names=["train", "val"],
        callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=True), lgb.log_evaluation(100)],
    )
    print(f"  Best iteration: {model.best_iteration}")

    predictions = model.predict(X_test)

    print("\n[4/4] Evaluating fukusho strategies...")
    print("=" * 60)

    results_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_fukusho.tsv")
    header = "experiment\troi\thit_rate\ttotal_bets_races\ttotal_bets_points\tannual_bets\tsharpe\tstatus\tdescription\tmonthly_roi_json\n"

    all_results = []

    # ===== Phase 1: ベースライン (n45フィルタそのまま) =====
    print("\n### Phase 1: n45フィルタ (MR=12, MO>=3.0) ###")
    for strat in ["top1", "top2", "top3", "top123"]:
        r = evaluate_fukusho(test, predictions, strategy=strat)
        r["name"] = f"f01_{strat}"
        r["desc"] = f"n45filter {strat} fukusho"
        all_results.append(r)
        print(f"  {strat:8s}: ROI={r['roi']:6.2f}% Hit={r['hit_rate']:5.2f}% Sharpe={r['sharpe']:+.3f} Races={r['total_bets_races']}")

    # ===== Phase 2: フィルタなし (全レース) =====
    print("\n### Phase 2: フィルタなし (全レース) ###")
    for strat in ["top1", "top2", "top3", "top123"]:
        r = evaluate_fukusho_v2(test, predictions, strategy=strat,
                                min_runners=0, min_top1_odds=0)
        if r:
            r["name"] = f"f02_{strat}"
            r["desc"] = f"nofilter {strat} fukusho"
            all_results.append(r)
            print(f"  {strat:8s}: ROI={r['roi']:6.2f}% Hit={r['hit_rate']:5.2f}% Sharpe={r['sharpe']:+.3f} Races={r['total_bets_races']}")

    # ===== Phase 3: 堅いレース限定 (1番人気オッズ < 3.0) =====
    print("\n### Phase 3: 堅いレース (1番人気オッズ < 3.0) ###")
    for strat in ["top1", "top2", "top3", "top123"]:
        r = evaluate_fukusho_v2(test, predictions, strategy=strat,
                                min_runners=0, max_top1_odds=3.0, min_top1_odds=0)
        if r:
            r["name"] = f"f03_{strat}"
            r["desc"] = f"solid_race(<3.0) {strat} fukusho"
            all_results.append(r)
            print(f"  {strat:8s}: ROI={r['roi']:6.2f}% Hit={r['hit_rate']:5.2f}% Sharpe={r['sharpe']:+.3f} Races={r['total_bets_races']}")

    # ===== Phase 4: 少頭数レース (8頭以下 = 複勝3着以内が当たりやすい) =====
    print("\n### Phase 4: 少頭数 (<=10頭, フィルタなし) ###")
    for max_r in [8, 10]:
        for strat in ["top1", "top123"]:
            # 少頭数：頭数フィルタを逆にする
            df_small = test[test["race_id"].map(test.groupby("race_id").size()) <= max_r]
            if len(df_small) > 0:
                pred_small = predictions[test.index.isin(df_small.index)]
                r = evaluate_fukusho_v2(df_small, pred_small, strategy=strat,
                                        min_runners=0, min_top1_odds=0)
                if r:
                    r["name"] = f"f04_{strat}_le{max_r}"
                    r["desc"] = f"<=  {max_r}頭 {strat} fukusho"
                    all_results.append(r)
                    print(f"  <={max_r}頭 {strat:8s}: ROI={r['roi']:6.2f}% Hit={r['hit_rate']:5.2f}% Sharpe={r['sharpe']:+.3f} Races={r['total_bets_races']}")

    # ===== Phase 5: 多頭数レース (14頭以上) =====
    print("\n### Phase 5: 多頭数 (>=14頭) ###")
    for strat in ["top1", "top123"]:
        r = evaluate_fukusho_v2(test, predictions, strategy=strat,
                                min_runners=14, min_top1_odds=0)
        if r:
            r["name"] = f"f05_{strat}"
            r["desc"] = f">=14頭 {strat} fukusho"
            all_results.append(r)
            print(f"  {strat:8s}: ROI={r['roi']:6.2f}% Hit={r['hit_rate']:5.2f}% Sharpe={r['sharpe']:+.3f} Races={r['total_bets_races']}")

    # ===== Phase 6: 複勝オッズ下限フィルタ (高配当複勝狙い) =====
    print("\n### Phase 6: 複勝オッズ下限フィルタ (TOP1) ###")
    for min_fo in [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        r = evaluate_fukusho_v2(test, predictions, strategy="top1_filtered",
                                min_runners=0, min_top1_odds=0, min_fukusho_odds=min_fo)
        if r:
            r["name"] = f"f06_fo{min_fo}"
            r["desc"] = f"TOP1 fukusho_odds>={min_fo}"
            all_results.append(r)
            print(f"  FO>={min_fo}: ROI={r['roi']:6.2f}% Hit={r['hit_rate']:5.2f}% Sharpe={r['sharpe']:+.3f} Races={r['total_bets_races']}")

    # ===== Phase 7: モデルスコア上位の信頼度フィルタ =====
    print("\n### Phase 7: モデルスコア信頼度フィルタ (TOP1) ###")
    df_temp = test[["race_id", "date", "umaban", "odds", "finish",
                     "fukusho_payout", "fukusho_odds_lower"]].copy()
    df_temp["pred"] = predictions

    for percentile in [50, 60, 70, 80, 90]:
        threshold = np.percentile(predictions, percentile)
        total_w, total_r, ht, bt, race_res = 0, 0, 0, 0, []
        for race_id, group in df_temp.groupby("race_id"):
            group = group.sample(frac=1, random_state=42)
            top1 = group.nlargest(1, "pred").iloc[0]
            if top1["pred"] < threshold:
                continue
            bt += 1
            total_w += 100
            if top1["finish"] <= 3 and top1["fukusho_payout"] > 0:
                total_r += top1["fukusho_payout"]
                ht += 1
            race_res.append((race_id, group["date"].iloc[0], 100,
                             top1["fukusho_payout"] if (top1["finish"] <= 3 and top1["fukusho_payout"] > 0) else 0))

        if total_w > 0 and len(race_res) > 1:
            roi = total_r / total_w * 100
            hit = ht / bt * 100 if bt > 0 else 0
            dates = [d for _,d,_,_ in race_res]
            yrs = max(((max(dates)-min(dates)).days+1)/365.25, 0.1)
            ann = len(race_res)/yrs
            rr_df = pd.DataFrame(race_res, columns=["r","date","w","ret"])
            rr_df["ym"] = rr_df["date"].dt.to_period("M")
            mo = rr_df.groupby("ym").agg(w=("w","sum"),ret=("ret","sum"))
            mo["roi"] = mo["ret"]/mo["w"]*100
            mr = mo["roi"].values - 100
            sh = np.mean(mr)/np.std(mr)*math.sqrt(12) if len(mr)>1 and np.std(mr)>0 else 0
            monthly_d = {str(k):round(v,2) for k,v in mo["roi"].items()}

            r = {"roi": round(roi,2), "hit_rate": round(hit,2), "total_bets_races": len(race_res),
                 "total_bets_points": bt, "annual_bets": round(ann,1), "sharpe": round(sh,3),
                 "total_return": int(total_r), "total_wagered": total_w, "profit": total_r-total_w,
                 "monthly_roi": monthly_d, "name": f"f07_p{percentile}", "desc": f"TOP1 pred>p{percentile}({threshold:.3f})"}
            all_results.append(r)
            print(f"  p{percentile} (>{threshold:.3f}): ROI={roi:6.2f}% Hit={hit:5.2f}% Sharpe={sh:+.3f} Races={len(race_res)} Ann={ann:.0f}")

    # ===== Phase 8: 堅レース + 高信頼度 組み合わせ =====
    print("\n### Phase 8: 堅レース + 高信頼度 ###")
    for max_o in [2.0, 3.0, 5.0]:
        for percentile in [50, 70, 80]:
            threshold = np.percentile(predictions, percentile)
            total_w, total_r, ht, bt, race_res = 0, 0, 0, 0, []
            for race_id, group in df_temp.groupby("race_id"):
                min_o = group["odds"].min()
                if min_o > max_o:
                    continue
                group = group.sample(frac=1, random_state=42)
                top1 = group.nlargest(1, "pred").iloc[0]
                if top1["pred"] < threshold:
                    continue
                bt += 1
                total_w += 100
                ret = 0
                if top1["finish"] <= 3 and top1["fukusho_payout"] > 0:
                    ret = top1["fukusho_payout"]
                    total_r += ret
                    ht += 1
                race_res.append((race_id, group["date"].iloc[0], 100, ret))

            if total_w > 0 and len(race_res) > 1:
                roi = total_r / total_w * 100
                hit = ht / bt * 100 if bt > 0 else 0
                dates = [d for _,d,_,_ in race_res]
                yrs = max(((max(dates)-min(dates)).days+1)/365.25, 0.1)
                ann = len(race_res)/yrs
                rr_df = pd.DataFrame(race_res, columns=["r","date","w","ret"])
                rr_df["ym"] = rr_df["date"].dt.to_period("M")
                mo = rr_df.groupby("ym").agg(w=("w","sum"),ret=("ret","sum"))
                mo["roi"] = mo["ret"]/mo["w"]*100
                mr = mo["roi"].values - 100
                sh = np.mean(mr)/np.std(mr)*math.sqrt(12) if len(mr)>1 and np.std(mr)>0 else 0
                monthly_d = {str(k):round(v,2) for k,v in mo["roi"].items()}

                r = {"roi": round(roi,2), "hit_rate": round(hit,2), "total_bets_races": len(race_res),
                     "total_bets_points": bt, "annual_bets": round(ann,1), "sharpe": round(sh,3),
                     "total_return": int(total_r), "total_wagered": total_w, "profit": total_r-total_w,
                     "monthly_roi": monthly_d, "name": f"f08_o{max_o}_p{percentile}",
                     "desc": f"TOP1 odds1<={max_o}+pred>p{percentile}"}
                all_results.append(r)
                print(f"  odds1<={max_o} p{percentile}: ROI={roi:6.2f}% Hit={hit:5.2f}% Sharpe={sh:+.3f} Races={len(race_res)} Ann={ann:.0f}")

    # ===== Phase 9: 高配当選択戦略 =====
    print("\n### Phase 9: 高配当選択戦略 ###")

    # 9a: TOP1-3の中で最も複勝オッズが高い馬1点
    for top_n in [2, 3, 4, 5]:
        total_w, total_r, ht, bt, race_res = 0, 0, 0, 0, []
        for race_id, group in df_temp.groupby("race_id"):
            group = group.sample(frac=1, random_state=42)
            topN = group.nlargest(top_n, "pred")
            # 複勝オッズが最も高い馬を選ぶ
            best = topN.loc[topN["fukusho_odds_lower"].idxmax()]
            bt += 1
            total_w += 100
            ret = 0
            if best["finish"] <= 3 and best["fukusho_payout"] > 0:
                ret = best["fukusho_payout"]
                total_r += ret
                ht += 1
            race_res.append((race_id, group["date"].iloc[0], 100, ret))
        if total_w > 0 and len(race_res) > 1:
            roi = total_r / total_w * 100
            hit = ht / bt * 100
            dates = [d for _,d,_,_ in race_res]
            yrs = max(((max(dates)-min(dates)).days+1)/365.25, 0.1)
            ann = len(race_res)/yrs
            rr_df2 = pd.DataFrame(race_res, columns=["r","date","w","ret"])
            rr_df2["ym"] = rr_df2["date"].dt.to_period("M")
            mo = rr_df2.groupby("ym").agg(w=("w","sum"),ret=("ret","sum"))
            mo["roi"] = mo["ret"]/mo["w"]*100
            mr = mo["roi"].values - 100
            sh = np.mean(mr)/np.std(mr)*math.sqrt(12) if len(mr)>1 and np.std(mr)>0 else 0
            monthly_d = {str(k):round(v,2) for k,v in mo["roi"].items()}
            r = {"roi": round(roi,2), "hit_rate": round(hit,2), "total_bets_races": len(race_res),
                 "total_bets_points": bt, "annual_bets": round(ann,1), "sharpe": round(sh,3),
                 "total_return": int(total_r), "total_wagered": total_w, "profit": total_r-total_w,
                 "monthly_roi": monthly_d, "name": f"f09a_maxfo_top{top_n}",
                 "desc": f"TOP{top_n}中max複勝odds 1pt"}
            all_results.append(r)
            print(f"  TOP{top_n}中max複勝odds: ROI={roi:6.2f}% Hit={hit:5.2f}% Sharpe={sh:+.3f} Races={len(race_res)}")

    # 9b: 期待値ベース（pred × 複勝オッズ下限が最大の馬）
    for top_n in [3, 5, 8]:
        total_w, total_r, ht, bt, race_res = 0, 0, 0, 0, []
        for race_id, group in df_temp.groupby("race_id"):
            group = group.sample(frac=1, random_state=42)
            topN = group.nlargest(top_n, "pred").copy()
            topN["ev"] = topN["pred"] * topN["fukusho_odds_lower"]
            best = topN.loc[topN["ev"].idxmax()]
            bt += 1
            total_w += 100
            ret = 0
            if best["finish"] <= 3 and best["fukusho_payout"] > 0:
                ret = best["fukusho_payout"]
                total_r += ret
                ht += 1
            race_res.append((race_id, group["date"].iloc[0], 100, ret))
        if total_w > 0 and len(race_res) > 1:
            roi = total_r / total_w * 100
            hit = ht / bt * 100
            dates = [d for _,d,_,_ in race_res]
            yrs = max(((max(dates)-min(dates)).days+1)/365.25, 0.1)
            ann = len(race_res)/yrs
            rr_df2 = pd.DataFrame(race_res, columns=["r","date","w","ret"])
            rr_df2["ym"] = rr_df2["date"].dt.to_period("M")
            mo = rr_df2.groupby("ym").agg(w=("w","sum"),ret=("ret","sum"))
            mo["roi"] = mo["ret"]/mo["w"]*100
            mr = mo["roi"].values - 100
            sh = np.mean(mr)/np.std(mr)*math.sqrt(12) if len(mr)>1 and np.std(mr)>0 else 0
            monthly_d = {str(k):round(v,2) for k,v in mo["roi"].items()}
            r = {"roi": round(roi,2), "hit_rate": round(hit,2), "total_bets_races": len(race_res),
                 "total_bets_points": bt, "annual_bets": round(ann,1), "sharpe": round(sh,3),
                 "total_return": int(total_r), "total_wagered": total_w, "profit": total_r-total_w,
                 "monthly_roi": monthly_d, "name": f"f09b_ev_top{top_n}",
                 "desc": f"TOP{top_n}中max期待値(pred×FO) 1pt"}
            all_results.append(r)
            print(f"  TOP{top_n}中max EV: ROI={roi:6.2f}% Hit={hit:5.2f}% Sharpe={sh:+.3f} Races={len(race_res)}")

    # 9c: 妙味スコア（モデル順位 vs 人気順位の乖離）
    # モデルTOP1-5の中で、人気順位が低い（人気がない）馬を選ぶ
    print("\n### Phase 9c: 妙味スコア ###")
    for top_n in [3, 5, 8]:
        total_w, total_r, ht, bt, race_res = 0, 0, 0, 0, []
        for race_id, group in df_temp.groupby("race_id"):
            group = group.sample(frac=1, random_state=42)
            # モデル順位
            group = group.copy()
            group["model_rank"] = group["pred"].rank(ascending=False)
            # 人気順位（オッズが低い=人気高い）
            group["popularity_rank"] = group["odds"].rank(ascending=True)
            topN = group.nlargest(top_n, "pred").copy()
            # 妙味 = 人気順位 - モデル順位 (正=モデルが過小評価されている)
            topN["mispricing"] = topN["popularity_rank"] - topN["model_rank"]
            best = topN.loc[topN["mispricing"].idxmax()]
            bt += 1
            total_w += 100
            ret = 0
            if best["finish"] <= 3 and best["fukusho_payout"] > 0:
                ret = best["fukusho_payout"]
                total_r += ret
                ht += 1
            race_res.append((race_id, group["date"].iloc[0], 100, ret))
        if total_w > 0 and len(race_res) > 1:
            roi = total_r / total_w * 100
            hit = ht / bt * 100
            dates = [d for _,d,_,_ in race_res]
            yrs = max(((max(dates)-min(dates)).days+1)/365.25, 0.1)
            ann = len(race_res)/yrs
            rr_df2 = pd.DataFrame(race_res, columns=["r","date","w","ret"])
            rr_df2["ym"] = rr_df2["date"].dt.to_period("M")
            mo = rr_df2.groupby("ym").agg(w=("w","sum"),ret=("ret","sum"))
            mo["roi"] = mo["ret"]/mo["w"]*100
            mr = mo["roi"].values - 100
            sh = np.mean(mr)/np.std(mr)*math.sqrt(12) if len(mr)>1 and np.std(mr)>0 else 0
            monthly_d = {str(k):round(v,2) for k,v in mo["roi"].items()}
            r = {"roi": round(roi,2), "hit_rate": round(hit,2), "total_bets_races": len(race_res),
                 "total_bets_points": bt, "annual_bets": round(ann,1), "sharpe": round(sh,3),
                 "total_return": int(total_r), "total_wagered": total_w, "profit": total_r-total_w,
                 "monthly_roi": monthly_d, "name": f"f09c_misp_top{top_n}",
                 "desc": f"TOP{top_n}中max妙味(人気順位-model順位) 1pt"}
            all_results.append(r)
            print(f"  TOP{top_n}中max妙味: ROI={roi:6.2f}% Hit={hit:5.2f}% Sharpe={sh:+.3f} Races={len(race_res)}")

    # 9d: 妙味スコア + 閾値フィルタ（妙味が大きいレースだけ買う）
    print("\n### Phase 9d: 妙味フィルタ付き ###")
    for top_n in [3, 5]:
        for min_misp in [2, 3, 5, 8]:
            total_w, total_r, ht, bt, race_res = 0, 0, 0, 0, []
            for race_id, group in df_temp.groupby("race_id"):
                group = group.sample(frac=1, random_state=42).copy()
                group["model_rank"] = group["pred"].rank(ascending=False)
                group["popularity_rank"] = group["odds"].rank(ascending=True)
                topN = group.nlargest(top_n, "pred").copy()
                topN["mispricing"] = topN["popularity_rank"] - topN["model_rank"]
                best = topN.loc[topN["mispricing"].idxmax()]
                if best["mispricing"] < min_misp:
                    continue
                bt += 1
                total_w += 100
                ret = 0
                if best["finish"] <= 3 and best["fukusho_payout"] > 0:
                    ret = best["fukusho_payout"]
                    total_r += ret
                    ht += 1
                race_res.append((race_id, group["date"].iloc[0], 100, ret))
            if total_w > 0 and len(race_res) > 1:
                roi = total_r / total_w * 100
                hit = ht / bt * 100
                dates = [d for _,d,_,_ in race_res]
                yrs = max(((max(dates)-min(dates)).days+1)/365.25, 0.1)
                ann = len(race_res)/yrs
                rr_df2 = pd.DataFrame(race_res, columns=["r","date","w","ret"])
                rr_df2["ym"] = rr_df2["date"].dt.to_period("M")
                mo = rr_df2.groupby("ym").agg(w=("w","sum"),ret=("ret","sum"))
                mo["roi"] = mo["ret"]/mo["w"]*100
                mr = mo["roi"].values - 100
                sh = np.mean(mr)/np.std(mr)*math.sqrt(12) if len(mr)>1 and np.std(mr)>0 else 0
                monthly_d = {str(k):round(v,2) for k,v in mo["roi"].items()}
                r = {"roi": round(roi,2), "hit_rate": round(hit,2), "total_bets_races": len(race_res),
                     "total_bets_points": bt, "annual_bets": round(ann,1), "sharpe": round(sh,3),
                     "total_return": int(total_r), "total_wagered": total_w, "profit": total_r-total_w,
                     "monthly_roi": monthly_d, "name": f"f09d_misp{min_misp}_top{top_n}",
                     "desc": f"TOP{top_n}中妙味>={min_misp}のみ 1pt"}
                all_results.append(r)
                print(f"  TOP{top_n} 妙味>={min_misp}: ROI={roi:6.2f}% Hit={hit:5.2f}% Sharpe={sh:+.3f} Races={len(race_res)} Ann={ann:.0f}")

    # 9e: EV最大 + レースフィルタ（荒れレース限定、多頭数）
    print("\n### Phase 9e: 高配当×荒れレース ###")
    for min_odds1 in [3.0, 5.0, 8.0]:
        for top_n in [3, 5]:
            total_w, total_r, ht, bt, race_res = 0, 0, 0, 0, []
            for race_id, group in df_temp.groupby("race_id"):
                if group["odds"].min() < min_odds1:
                    continue
                if len(group) < 12:
                    continue
                group = group.sample(frac=1, random_state=42)
                topN = group.nlargest(top_n, "pred").copy()
                topN["ev"] = topN["pred"] * topN["fukusho_odds_lower"]
                best = topN.loc[topN["ev"].idxmax()]
                bt += 1
                total_w += 100
                ret = 0
                if best["finish"] <= 3 and best["fukusho_payout"] > 0:
                    ret = best["fukusho_payout"]
                    total_r += ret
                    ht += 1
                race_res.append((race_id, group["date"].iloc[0], 100, ret))
            if total_w > 0 and len(race_res) > 1:
                roi = total_r / total_w * 100
                hit = ht / bt * 100
                dates = [d for _,d,_,_ in race_res]
                yrs = max(((max(dates)-min(dates)).days+1)/365.25, 0.1)
                ann = len(race_res)/yrs
                rr_df2 = pd.DataFrame(race_res, columns=["r","date","w","ret"])
                rr_df2["ym"] = rr_df2["date"].dt.to_period("M")
                mo = rr_df2.groupby("ym").agg(w=("w","sum"),ret=("ret","sum"))
                mo["roi"] = mo["ret"]/mo["w"]*100
                mr = mo["roi"].values - 100
                sh = np.mean(mr)/np.std(mr)*math.sqrt(12) if len(mr)>1 and np.std(mr)>0 else 0
                monthly_d = {str(k):round(v,2) for k,v in mo["roi"].items()}
                r = {"roi": round(roi,2), "hit_rate": round(hit,2), "total_bets_races": len(race_res),
                     "total_bets_points": bt, "annual_bets": round(ann,1), "sharpe": round(sh,3),
                     "total_return": int(total_r), "total_wagered": total_w, "profit": total_r-total_w,
                     "monthly_roi": monthly_d, "name": f"f09e_o{min_odds1}_top{top_n}",
                     "desc": f"荒れ(odds1>={min_odds1})TOP{top_n}中maxEV 1pt"}
                all_results.append(r)
                print(f"  odds1>={min_odds1} TOP{top_n} maxEV: ROI={roi:6.2f}% Hit={hit:5.2f}% Sharpe={sh:+.3f} Races={len(race_res)} Ann={ann:.0f}")

    # ===== Save all results =====
    with open(results_path, "w") as f:
        f.write(header)
        for r in all_results:
            roi = r["roi"]
            hit = r["hit_rate"]
            sharpe = r["sharpe"]
            if hit >= 30.0 and roi > 100.0:
                status = "keep"
            else:
                status = "discard"
            monthly_json = json.dumps(r.get("monthly_roi", {}), ensure_ascii=False)
            line = f"{r['name']}\t{roi}\t{hit}\t{r['total_bets_races']}\t{r['total_bets_points']}\t{r['annual_bets']}\t{sharpe}\t{status}\t{r['desc']}\t{monthly_json}\n"
            f.write(line)

    print(f"\n\nResults saved to {results_path}")
    print(f"Total time: {time.time()-t0:.1f}s")

    # Show best results
    print("\n===== TOP 10 by ROI =====")
    keeps = [r for r in all_results if r["hit_rate"] >= 30.0]
    keeps.sort(key=lambda x: -x["roi"])
    for r in keeps[:10]:
        print(f"  {r['name']:25s} ROI={r['roi']:6.2f}% Hit={r['hit_rate']:5.2f}% Sharpe={r['sharpe']:+.3f} Races={r['total_bets_races']}")

    return all_results

if __name__ == "__main__":
    main()
