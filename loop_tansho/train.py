"""
競馬回収率改善ループ — 特徴量・モデル・戦略（このファイルを編集する）
Usage: uv run train.py
"""

import os
import sys
import time
import json

import numpy as np
import pandas as pd
import lightgbm as lgb

# prepare.py は固定ファイル — 変更しない
from prepare import (
    load_raw_data, split_train_test, get_feature_columns,
    evaluate, print_summary, log_result, init_results, plot_results,
    ExperimentResult,
)

# ---------------------------------------------------------------------------
# Hyperparameters (edit these directly)
# ---------------------------------------------------------------------------

LGBM_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "learning_rate": 0.05,
    "num_leaves": 31,
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

# 賭け戦略
BET_STRATEGY = "ev_top_per_race"
BET_THRESHOLD = 0.40
BET_TOP_N = 1
BET_EV_THRESHOLD = 1.13      # EV閾値
BET_EV_THRESHOLD_DIRT = 1.13 # ダート用（同じ）
MIN_ODDS = 5.5
MAX_ODDS = 55.0
TURF_ONLY = False           # 全レース
MIN_RUNNERS = 12            # 最低出走頭数

# ---------------------------------------------------------------------------
# Feature Engineering (edit this function)
# ---------------------------------------------------------------------------

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    特徴量エンジニアリング。
    ここを編集して回収率を改善する。
    注意: レース結果に関するカラムを使わないこと！
    """
    df = df.copy()

    # --- カテゴリカル変数のエンコーディング ---
    # 場所
    if "場所" in df.columns:
        df["場所_code"] = df["場所"].astype("category").cat.codes

    # 性別
    if "性別" in df.columns:
        df["性別_code"] = df["性別"].astype("category").cat.codes

    # 芝・ダ
    if "芝・ダ" in df.columns:
        df["芝ダ_code"] = df["芝・ダ"].astype("category").cat.codes

    # 馬場状態
    if "馬場状態" in df.columns:
        df["馬場_code"] = df["馬場状態"].astype("category").cat.codes

    # 天気
    if "天気" in df.columns:
        df["天気_code"] = df["天気"].astype("category").cat.codes

    # コース区分
    if "コース区分" in df.columns:
        df["コース区分_code"] = df["コース区分"].astype("category").cat.codes

    # 所属
    if "所属" in df.columns:
        df["所属_code"] = df["所属"].astype("category").cat.codes

    # --- 基本的な派生特徴量 ---
    # 人気とオッズの関係
    if "人気" in df.columns and "odds" in df.columns:
        df["人気_odds_ratio"] = df["人気"] / df["odds"].clip(lower=1)
        df["log_odds"] = np.log1p(df["odds"])

    # 枠番と馬番の差
    if "枠番" in df.columns and "馬番" in df.columns:
        df["枠馬番差"] = df["馬番"] - df["枠番"]

    # 馬番/頭数 比率
    if "馬番" in df.columns and "頭数" in df.columns:
        df["馬番比率"] = df["馬番"] / df["頭数"].clip(lower=1)

    # 斤量関連
    if "斤量" in df.columns and "馬齢斤量差" in df.columns:
        df["斤量_num"] = pd.to_numeric(df["斤量"], errors="coerce")
        df["馬齢斤量差_num"] = pd.to_numeric(df["馬齢斤量差"], errors="coerce")
        df["斤量_adjusted"] = df["斤量_num"] + df["馬齢斤量差_num"]

    return df


# ---------------------------------------------------------------------------
# Bet Selection (edit this function)
# ---------------------------------------------------------------------------

def select_bets(df_test: pd.DataFrame, predictions: np.ndarray) -> np.ndarray:
    """
    賭ける馬を選択する。
    predictions: モデルの予測確率（1着になる確率）

    Returns: boolean配列（True=賭ける）
    """
    mask = np.zeros(len(df_test), dtype=bool)

    # オッズフィルター
    odds_ok = (df_test["odds"].values >= MIN_ODDS) & (df_test["odds"].values <= MAX_ODDS)

    odds = df_test["odds"].values

    if BET_STRATEGY == "threshold":
        # 予測確率が閾値以上のものに賭ける
        mask = (predictions >= BET_THRESHOLD) & odds_ok

    elif BET_STRATEGY == "expected_value":
        # 期待値ベース: pred * odds(倍率) > EV閾値
        ev = predictions * odds
        mask = (ev >= BET_EV_THRESHOLD) & odds_ok

    elif BET_STRATEGY == "hybrid":
        # ハイブリッド: EV条件 OR 高確信度の人気馬
        ev = predictions * odds
        ev_bet = (ev >= BET_EV_THRESHOLD) & odds_ok
        confident_bet = (predictions >= BET_THRESHOLD) & odds_ok
        mask = ev_bet | confident_bet

    elif BET_STRATEGY == "ev_top_per_race":
        # レースごとにEV最高の1頭に賭ける（EV閾値以上のもののみ）
        df_temp = df_test[["race_id", "odds"]].copy()
        if "芝・ダ" in df_test.columns:
            df_temp["is_turf"] = (df_test["芝・ダ"] == "芝").values
        else:
            df_temp["is_turf"] = True
        df_temp["pred"] = predictions
        df_temp["ev"] = predictions * odds
        df_temp["idx"] = range(len(df_temp))
        df_temp["odds_ok"] = odds_ok

        for race_id, group in df_temp.groupby("race_id"):
            if TURF_ONLY and not group["is_turf"].iloc[0]:
                continue
            if len(group) < MIN_RUNNERS:
                continue
            # 芝/ダートで異なるEV閾値
            is_turf = group["is_turf"].iloc[0]
            ev_thresh = BET_EV_THRESHOLD if is_turf else BET_EV_THRESHOLD_DIRT
            eligible = group[group["odds_ok"] & (group["ev"] >= ev_thresh)]
            if len(eligible) == 0:
                continue
            best = eligible.loc[eligible["ev"].idxmax()]
            mask[int(best["idx"])] = True

    elif BET_STRATEGY == "ev_race_quality":
        # レースの「質」で選ぶ: EV上位2馬の平均EVが閾値以上なら最高EVに賭ける
        df_temp = df_test[["race_id", "odds"]].copy()
        df_temp["pred"] = predictions
        df_temp["ev"] = predictions * odds
        df_temp["idx"] = range(len(df_temp))
        df_temp["odds_ok"] = odds_ok

        for race_id, group in df_temp.groupby("race_id"):
            eligible = group[group["odds_ok"]]
            if len(eligible) < 2:
                continue
            top2 = eligible.nlargest(2, "ev")
            avg_ev = top2["ev"].mean()
            if avg_ev >= BET_EV_THRESHOLD:
                best = top2.iloc[0]
                mask[int(best["idx"])] = True

    elif BET_STRATEGY == "two_tier":
        # Tier 1: 中穴帯 — EV top per race
        df_temp = df_test[["race_id", "odds"]].copy()
        df_temp["pred"] = predictions
        df_temp["ev"] = predictions * odds
        df_temp["idx"] = range(len(df_temp))

        tier1_ok = (odds >= TIER1_MIN_ODDS) & (odds <= TIER1_MAX_ODDS)
        for race_id, group in df_temp.groupby("race_id"):
            eligible = group[tier1_ok[group["idx"].values] & (group["ev"] >= BET_EV_THRESHOLD)]
            if len(eligible) == 0:
                continue
            best = eligible.loc[eligible["ev"].idxmax()]
            mask[int(best["idx"])] = True

        # Tier 2: 人気馬帯 — 高確信度
        tier2_ok = (odds >= TIER2_MIN_ODDS) & (odds <= TIER2_MAX_ODDS)
        tier2_mask = (predictions >= BET_THRESHOLD) & tier2_ok
        mask = mask | tier2_mask

    elif BET_STRATEGY == "prob_top_ev_filter":
        # レースごとに予測確率最高の1頭を選び、EV閾値以上なら賭ける
        df_temp = df_test[["race_id", "odds"]].copy()
        df_temp["pred"] = predictions
        df_temp["ev"] = predictions * odds
        df_temp["idx"] = range(len(df_temp))
        df_temp["odds_ok"] = odds_ok

        for race_id, group in df_temp.groupby("race_id"):
            eligible = group[group["odds_ok"]]
            if len(eligible) == 0:
                continue
            # 予測確率最高の馬
            best = eligible.loc[eligible["pred"].idxmax()]
            if best["ev"] >= BET_EV_THRESHOLD:
                mask[int(best["idx"])] = True

    elif BET_STRATEGY == "top_n":
        # レースごとに上位N頭に賭ける
        df_temp = df_test.copy()
        df_temp["pred"] = predictions
        df_temp["idx"] = range(len(df_temp))

        for race_id, group in df_temp.groupby("race_id"):
            group_odds = odds_ok[group["idx"].values]
            eligible = group[group_odds]
            if len(eligible) == 0:
                continue
            top_n = eligible.nlargest(BET_TOP_N, "pred")
            mask[top_n["idx"].values] = True

    return mask


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    t_start = time.time()

    print("=" * 60)
    print("競馬回収率改善ループ — 実験実行")
    print("=" * 60)

    # データ読み込み
    print("\n[1/5] Loading data...")
    df = load_raw_data()
    print(f"  Total records: {len(df):,}")

    # 特徴量エンジニアリング
    print("\n[2/5] Engineering features...")
    df = engineer_features(df)

    # 時系列分割
    train, test = split_train_test(df)
    print(f"  Train: {len(train):,} records ({train['date'].min().date()} ~ {train['date'].max().date()})")
    print(f"  Test:  {len(test):,} records ({test['date'].min().date()} ~ {test['date'].max().date()})")

    # 特徴量カラム取得
    feature_cols = get_feature_columns(df)
    print(f"  Features: {len(feature_cols)} columns")

    # 欠損値の処理（LightGBMは欠損を扱えるのでそのまま）
    y_train_all = train["win"].values
    y_test_all = test["win"].values

    # Validation用に学習データの最後の20%を使う
    val_size = int(len(y_train_all) * 0.2)

    # --- 特徴量選択 (TOP_K_FEATURES > 0 なら2段階学習) ---
    TOP_K_FEATURES = 0  # 0=全特徴量, >0=重要度上位K個で再学習
    if TOP_K_FEATURES > 0:
        print(f"\n  [Feature Selection] Training initial model to select top {TOP_K_FEATURES} features...")
        X_tmp = train[feature_cols].values
        dtmp = lgb.Dataset(X_tmp[:-val_size], label=y_train_all[:-val_size],
                           feature_name=feature_cols, free_raw_data=False)
        dvtmp = lgb.Dataset(X_tmp[-val_size:], label=y_train_all[-val_size:],
                            feature_name=feature_cols, free_raw_data=False)
        m_tmp = lgb.train(LGBM_PARAMS, dtmp, num_boost_round=NUM_BOOST_ROUND,
                          valid_sets=[dvtmp], valid_names=["val"],
                          callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                                     lgb.log_evaluation(period=0)])
        imp = m_tmp.feature_importance(importance_type="gain")
        ranked = sorted(zip(feature_cols, imp), key=lambda x: -x[1])
        feature_cols = [name for name, _ in ranked[:TOP_K_FEATURES]]
        print(f"  Selected {len(feature_cols)} features")

    X_train = train[feature_cols].values
    y_train = y_train_all
    X_test = test[feature_cols].values
    y_test = y_test_all

    X_val = X_train[-val_size:]
    y_val = y_train[-val_size:]
    X_train_fit = X_train[:-val_size]
    y_train_fit = y_train[:-val_size]

    # LightGBM訓練（複数seedアンサンブル）
    ENSEMBLE_SEEDS = [42, 123, 7]
    print(f"\n[3/5] Training LightGBM (ensemble of {len(ENSEMBLE_SEEDS)} seeds)...")
    dtrain = lgb.Dataset(X_train_fit, label=y_train_fit, feature_name=feature_cols, free_raw_data=False)
    dval = lgb.Dataset(X_val, label=y_val, feature_name=feature_cols, free_raw_data=False)

    all_predictions = []
    for seed_i, seed in enumerate(ENSEMBLE_SEEDS):
        params = LGBM_PARAMS.copy()
        params["seed"] = seed

        callbacks = [
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=(seed_i == 0)),
            lgb.log_evaluation(period=100 if seed_i == 0 else 0),
        ]

        model = lgb.train(
            params,
            dtrain,
            num_boost_round=NUM_BOOST_ROUND,
            valid_sets=[dtrain, dval],
            valid_names=["train", "val"],
            callbacks=callbacks,
        )
        print(f"  Seed {seed}: best_iteration={model.best_iteration}")
        all_predictions.append(model.predict(X_test))

        if seed_i == 0:
            # Feature importance from first model
            importance = model.feature_importance(importance_type="gain")
            feat_imp = sorted(zip(feature_cols, importance), key=lambda x: -x[1])
            print("\n  Top 15 features (gain):")
            for name, imp in feat_imp[:15]:
                print(f"    {name:30s} {imp:.1f}")

    # テスト予測（アンサンブル — seed42に重みを置く）
    print("\n[4/5] Predicting on test set (weighted ensemble)...")
    weights = [0.6, 0.2, 0.2]  # seed 42 gets more weight
    predictions = np.average(all_predictions, axis=0, weights=weights)

    # 賭け選択
    bet_mask = select_bets(test, predictions)
    print(f"  Bets selected: {bet_mask.sum():,} / {len(test):,}")

    # 評価
    print("\n[5/5] Evaluating...")
    result = evaluate(test, bet_mask)
    print_summary(result)

    # 月別ROI
    if result.monthly_roi:
        print("\nmonthly_roi:")
        for month, roi_val in sorted(result.monthly_roi.items()):
            marker = " ★" if roi_val >= 100 else ""
            print(f"  {month}: {roi_val:.1f}%{marker}")

    t_end = time.time()
    print(f"\ntotal_seconds:    {t_end - t_start:.1f}")

    return result


if __name__ == "__main__":
    main()
