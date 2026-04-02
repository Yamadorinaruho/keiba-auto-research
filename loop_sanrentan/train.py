"""
競馬三連単回収率改善ループ — 特徴量・モデル・戦略（このファイルを編集する）
Usage: uv run train.py
"""

import os
import sys
import time
import json
import math
from itertools import permutations

import numpy as np
import pandas as pd
import lightgbm as lgb

# prepare.py は固定ファイル — 変更しない
from prepare import (
    load_raw_data, split_train_test, get_feature_columns,
    build_race_trifecta, evaluate, print_summary,
    log_result, init_results, plot_results,
    ExperimentResult,
)

# ---------------------------------------------------------------------------
# Hyperparameters (edit these directly)
# ---------------------------------------------------------------------------

LGBM_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "learning_rate": 0.10,
    "num_leaves": 100,
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

# 三連単賭け戦略パラメータ
MIN_RACE_RUNNERS = 12   # 最低出走頭数
MIN_TOP1_ODDS = 4.0     # 1番人気の最低オッズ（堅いレースを除外）
VOLATILE_THRESHOLD = 0.0 # 全レースでTOP3ボックス6点
MIN_PRED_GAP = 0.02     # TOP3とTOP4の予測スコア差が小さいレースをスキップ

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
    if "場所" in df.columns:
        df["場所_code"] = df["場所"].astype("category").cat.codes

    if "性別" in df.columns:
        df["性別_code"] = df["性別"].astype("category").cat.codes

    if "芝・ダ" in df.columns:
        df["芝ダ_code"] = df["芝・ダ"].astype("category").cat.codes

    if "馬場状態" in df.columns:
        df["馬場_code"] = df["馬場状態"].astype("category").cat.codes

    if "天気" in df.columns:
        df["天気_code"] = df["天気"].astype("category").cat.codes

    if "コース区分" in df.columns:
        df["コース区分_code"] = df["コース区分"].astype("category").cat.codes

    if "所属" in df.columns:
        df["所属_code"] = df["所属"].astype("category").cat.codes

    # --- 最新時系オッズ（指時系4→3→2→1のフォールバック）---
    latest_odds = pd.Series(np.nan, index=df.index)
    latest_ninki = pd.Series(np.nan, index=df.index)
    for i in [1, 2, 3, 4]:  # 1から順に上書き→最後に残るのが最新の有効値
        col_odds = f"指時系{i}・単勝"
        col_ninki = f"指時系{i}・人気"
        if col_odds in df.columns:
            vals = pd.to_numeric(df[col_odds], errors="coerce")
            mask = vals > 0
            latest_odds = latest_odds.where(~mask, vals)
        if col_ninki in df.columns:
            vals = pd.to_numeric(df[col_ninki], errors="coerce")
            mask = vals > 0
            latest_ninki = latest_ninki.where(~mask, vals)
    df["latest_pre_odds"] = latest_odds
    df["log_latest_pre_odds"] = np.log1p(latest_odds)
    df["latest_pre_ninki"] = latest_ninki

    if "枠番" in df.columns and "馬番" in df.columns:
        df["枠馬番差"] = df["馬番"] - df["枠番"]

    if "馬番" in df.columns and "頭数" in df.columns:
        df["馬番比率"] = df["馬番"] / df["頭数"].clip(lower=1)

    if "斤量" in df.columns and "馬齢斤量差" in df.columns:
        df["斤量_num"] = pd.to_numeric(df["斤量"], errors="coerce")
        df["馬齢斤量差_num"] = pd.to_numeric(df["馬齢斤量差"], errors="coerce")
        df["斤量_adjusted"] = df["斤量_num"] + df["馬齢斤量差_num"]

    # --- 前走成績の派生特徴量 ---
    if "前走着順" in df.columns:
        df["前走着順_num"] = pd.to_numeric(df["前走着順"], errors="coerce")
        df["前走top3"] = (df["前走着順_num"] <= 3).astype(float)

    if "前走単勝オッズ" in df.columns:
        df["前走log_odds"] = np.log1p(pd.to_numeric(df["前走単勝オッズ"], errors="coerce"))

    if "前走着順" in df.columns and "前走人気" in df.columns:
        prev_finish = pd.to_numeric(df["前走着順"], errors="coerce")
        prev_ninki = pd.to_numeric(df["前走人気"], errors="coerce")
        df["前走着順_人気差"] = prev_finish - prev_ninki  # 負=人気以上の走り


    # --- 各時系の個別特徴量 ---
    for i in range(1, 5):
        col_odds = f"指時系{i}・単勝"
        col_ninki = f"指時系{i}・人気"
        if col_odds in df.columns:
            raw = pd.to_numeric(df[col_odds], errors="coerce").replace(0, np.nan)
            df[f"時系{i}_log_odds"] = np.log1p(raw)
        if col_ninki in df.columns:
            raw_n = pd.to_numeric(df[col_ninki], errors="coerce").replace(0, np.nan)
            df[f"時系{i}_ninki"] = raw_n

    # --- オッズ変動（時系1→最新の差）---
    if "指時系1・単勝" in df.columns:
        early_odds = pd.to_numeric(df["指時系1・単勝"], errors="coerce").replace(0, np.nan)
        df["オッズ変動_early_to_latest"] = df["latest_pre_odds"] - early_odds
        df["オッズ変動率_early_to_latest"] = df["latest_pre_odds"] / early_odds.clip(lower=0.1)

    # --- クロス特徴量 ---
    if "距離" in df.columns and "馬場_code" in df.columns:
        dist = pd.to_numeric(df["距離"], errors="coerce")
        df["距離x馬場"] = dist * df["馬場_code"]

    if "距離" in df.columns and "芝ダ_code" in df.columns:
        dist = pd.to_numeric(df["距離"], errors="coerce")
        df["距離x芝ダ"] = dist * df["芝ダ_code"]

    # --- 間隔（休み明け）---
    if "間隔" in df.columns:
        df["間隔_num"] = pd.to_numeric(df["間隔"], errors="coerce")
        df["休み明け"] = (df["間隔_num"] >= 10).astype(float)  # 10週以上

    # --- 血統タイプのカテゴリエンコーディング ---
    for col in ["父タイプ名", "母父タイプ名"]:
        if col in df.columns:
            df[f"{col}_code"] = df[col].astype("category").cat.codes

    # --- 人気（順位）---
    if "人気" in df.columns:
        df["人気_num"] = pd.to_numeric(df["人気"], errors="coerce")

    # --- 複勝シェア ---
    if "複勝シェア" in df.columns:
        df["複勝シェア_num"] = pd.to_numeric(df["複勝シェア"], errors="coerce")

    # --- 複勝オッズ下限 ---
    if "複勝オッズ下限" in df.columns:
        df["複勝オッズ下限_num"] = pd.to_numeric(df["複勝オッズ下限"], errors="coerce")

    return df


# ---------------------------------------------------------------------------
# Trifecta Bet Selection (edit this function)
# ---------------------------------------------------------------------------

def select_trifecta_bets(
    df_test: pd.DataFrame,
    predictions: np.ndarray,
) -> dict:
    """
    三連単の賭け組み合わせを選択する。

    Args:
        df_test: テスト期間のDataFrame
        predictions: モデルの予測スコア（高いほど好着順を予測）

    Returns:
        dict: {race_id: [(1着馬番, 2着馬番, 3着馬番), ...]}
    """
    df_temp = df_test[["race_id", "umaban", "odds"]].copy()
    df_temp["pred"] = predictions

    race_bets = {}

    for race_id, group in df_temp.groupby("race_id"):
        if len(group) < MIN_RACE_RUNNERS:
            continue
        min_odds_in_race = group["odds"].min()
        if min_odds_in_race < MIN_TOP1_ODDS:
            continue

        # 同スコア時の行順リークを防止するためシャッフル
        group = group.sample(frac=1, random_state=42)

        # TOP3とTOP4の予測スコア差が小さい→モデルの確信度が低い→スキップ
        # TOP3-TOP4ギャップとTOP1-TOP4ギャップの両方をチェック
        sorted_preds = group["pred"].sort_values(ascending=False).values
        if len(sorted_preds) >= 4 and (sorted_preds[2] - sorted_preds[3]) < MIN_PRED_GAP:
            continue
        # TOP1がTOP4から十分離れていない→3着以内の予測に自信なし→スキップ
        if len(sorted_preds) >= 4 and (sorted_preds[0] - sorted_preds[3]) < 0.07:
            continue

        # 動的点数: 荒れるレース→TOP3ボックス(6点)、堅め→1着固定(2点)
        if min_odds_in_race >= VOLATILE_THRESHOLD:
            top = group.nlargest(3, "pred")
            combos = list(permutations(top["umaban"].astype(int).values, 3))
        else:
            top3 = group.nlargest(3, "pred")
            hn = top3["umaban"].astype(int).values
            combos = [(hn[0], hn[1], hn[2]), (hn[0], hn[2], hn[1])]

        if combos:
            race_bets[race_id] = combos

    return race_bets


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    t_start = time.time()

    print("=" * 60)
    print("競馬三連単回収率改善ループ — 実験実行")
    print("=" * 60)

    # データ読み込み
    print("\n[1/6] Loading data...")
    df = load_raw_data()
    print(f"  Total records: {len(df):,}")

    # 特徴量エンジニアリング
    print("\n[2/6] Engineering features...")
    df = engineer_features(df)

    # 時系列分割
    train, test = split_train_test(df)
    print(f"  Train: {len(train):,} records ({train['date'].min().date()} ~ {train['date'].max().date()})")
    print(f"  Test:  {len(test):,} records ({test['date'].min().date()} ~ {test['date'].max().date()})")

    # 三連単の正解データを構築
    print("\n[3/6] Building trifecta ground truth...")
    race_trifecta = build_race_trifecta(test)
    print(f"  Races with trifecta result: {len(race_trifecta):,}")

    # 特徴量: 競馬ドメイン知識ベースの手動選択
    feature_cols = [
        # 今走の市場評価
        "単勝オッズ", "人気_num", "複勝シェア_num", "複勝オッズ下限_num",
        # 前走の実績
        "前走着順_num", "前走top3",
        # 前走の市場評価
        "前走log_odds", "前走着順_人気差",
        # コース条件
        "場所_code", "芝ダ_code", "馬場_code",
        # 馬の基本属性
        "年齢", "性別_code", "斤量_num",
        # レース条件
        "頭数", "馬番比率",
        # 状態
        "間隔_num", "走前",
        # 追加
        "枠番", "距離",
    ]
    # 存在しないカラムを除外
    feature_cols = [c for c in feature_cols if c in df.columns]
    print(f"  Features: {len(feature_cols)} columns (domain-selected)")

    # LightGBM用データ準備
    # Binary: 3着以内=1, それ以外=0
    train["target"] = (train["finish"] <= 3).astype(int)
    test["target"] = (test["finish"] <= 3).astype(int)

    X_train = train[feature_cols].values
    y_train = train["target"].values
    X_test = test[feature_cols].values

    # Validation用に学習データの最後の20%を使う
    val_size = int(len(X_train) * 0.2)
    X_train_fit = X_train[:-val_size]
    y_train_fit = y_train[:-val_size]
    X_val = X_train[-val_size:]
    y_val = y_train[-val_size:]

    # LightGBM訓練
    print("\n[4/6] Training LightGBM (binary)...")
    dtrain = lgb.Dataset(X_train_fit, label=y_train_fit, feature_name=feature_cols, free_raw_data=False)
    dval = lgb.Dataset(X_val, label=y_val, feature_name=feature_cols, free_raw_data=False)

    if NUM_BOOST_ROUND > 1:
        callbacks = [
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=True),
            lgb.log_evaluation(period=100),
        ]
        model = lgb.train(
            LGBM_PARAMS, dtrain, num_boost_round=NUM_BOOST_ROUND,
            valid_sets=[dtrain, dval], valid_names=["train", "val"],
            callbacks=callbacks,
        )
    else:
        model = lgb.train(
            LGBM_PARAMS, dtrain, num_boost_round=NUM_BOOST_ROUND,
        )
    print(f"  Best iteration: {model.best_iteration}")

    # Feature importance
    importance = model.feature_importance(importance_type="gain")
    feat_imp = sorted(zip(feature_cols, importance), key=lambda x: -x[1])
    print("\n  Top 15 features (gain):")
    for name, imp in feat_imp[:15]:
        print(f"    {name:30s} {imp:.1f}")

    # テスト予測
    print("\n[5/6] Predicting and selecting trifecta bets...")
    predictions = model.predict(X_test)

    # 三連単賭け選択
    race_bets = select_trifecta_bets(test, predictions)
    total_combos = sum(len(v) for v in race_bets.values())
    print(f"  Races to bet: {len(race_bets):,}")
    print(f"  Total combos: {total_combos:,}")
    if race_bets:
        print(f"  Avg combos/race: {total_combos / len(race_bets):.1f}")

    # 評価
    print("\n[6/6] Evaluating...")
    result = evaluate(test, race_bets, race_trifecta)
    print_summary(result)

    t_end = time.time()
    print(f"\ntotal_seconds:    {t_end - t_start:.1f}")

    # --- リスク指標算出 ---
    if result.monthly_roi and len(result.monthly_roi) > 1:
        values = np.array([result.monthly_roi[m] for m in sorted(result.monthly_roi.keys())])
        returns = (values - 100) / 100  # 月次超過リターン

        # Sortino（下方偏差のみ）
        downside = returns[returns < 0]
        downside_std = np.sqrt(np.mean(downside**2)) if len(downside) > 0 else 0
        sortino = np.mean(returns) / downside_std * math.sqrt(12) if downside_std > 0 else 0

        # MDD（月次累積P&Lベース）
        monthly_bets = result.annual_bets / 12
        monthly_wagered = monthly_bets * result.avg_combos_per_race * 100
        monthly_pnl = [(v / 100 - 1) * monthly_wagered for v in values]
        cum_pnl = np.cumsum(monthly_pnl)
        peak = np.maximum.accumulate(cum_pnl)
        dd_yen = cum_pnl - peak
        mdd_yen = abs(dd_yen.min())
        peak_at_mdd = peak[np.argmin(dd_yen)]
        mdd_pct = (mdd_yen / peak_at_mdd * 100) if peak_at_mdd > 0 else 0

        # 最大連続赤字月
        streak = max_streak = 0
        for r in returns:
            if r < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0

        # ケリー基準 & 破産確率
        p = result.hit_rate / 100
        if p > 0:
            b = result.roi / 100 / p - 1
            kelly = p - (1 - p) / b if b > 0 else 0
            ruin_factor = (1 - p) / (p * (1 + b)) if b > 0 else 1.0
            ruin_100 = ruin_factor ** 100 if ruin_factor < 1 else 1.0
        else:
            b = kelly = 0
            ruin_100 = 1.0

        # 年間利益
        annual_wagered = result.annual_bets * result.avg_combos_per_race * 100
        annual_profit = annual_wagered * (result.roi / 100 - 1)

        win_m = sum(1 for v in values if v >= 100)
        lose_m = sum(1 for v in values if v < 100)

        print(f"sortino:          {sortino:.3f}")
        print(f"mdd_pct:          {mdd_pct:.1f}%")
        print(f"mdd_yen:          {mdd_yen:,.0f}")
        print(f"max_losing_streak:{max_streak}")
        print(f"win_lose_months:  {win_m}/{lose_m}")
        print(f"kelly_fraction:   {kelly:.4f}")
        print(f"ruin_prob_100:    {ruin_100:.8f}")
        print(f"annual_profit:    {annual_profit:+,.0f}")
        print(f"monthly_roi_json: {json.dumps(result.monthly_roi, ensure_ascii=False)}")

    # 自動ログ
    results_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.tsv")
    init_results(results_path)

    return result


if __name__ == "__main__":
    main()
