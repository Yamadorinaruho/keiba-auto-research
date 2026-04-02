"""
競馬回収率改善ループ — データ読み込み・評価・可視化（固定ファイル）
このファイルは変更しない。train.py のみを編集する。

Usage: train.py から import して使う
"""

import os
import re
import json
import math
import warnings
from dataclasses import dataclass, asdict, field
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Data_2020_fixed.csv")

# 時系列分割境界
TRAIN_END = "2023.12.31"    # 学習データの最終日
TEST_START = "2024. 1. 1"   # テストデータの開始日

# 制約条件
MIN_HIT_RATE = 0.0          # 的中率制約なし
MIN_ANNUAL_BETS = 600       # 年間600レース以上に賭ける

# ---------------------------------------------------------------------------
# データリーク防止: レース結果に関するカラム（特徴量に使ってはいけない）
# ---------------------------------------------------------------------------

LEAKAGE_COLUMNS = [
    # 着順・順位系
    "着順", "確定着順", "入線順位",
    # タイム系
    "走破タイム", "走破タイム.1", "付加タイム", "着差", "着差.1",
    "上3F地点差", "上り3F", "上り3F順", "Ave-3F",
    "平均1Fタイム", "平均速度", "-3F平均速度", "上り3F平均速度",
    # 脚質・決め手系
    "脚質", "決め手", "コーナー",
    "2角", "3角", "4角", "1角",
    "2角.1", "3角.1", "4角.1",
    # 指数系（当日レース結果由来）
    "PCI", "好走", "PCI3", "RPCI",
    # 配当・賞金系
    "賞金", "付加賞金",
    "単勝配当", "複勝配当", "複勝人気.1",
    "枠連", "枠連人気", "馬連", "馬連人気",
    "馬単", "馬単人気", "3連複", "3連複人気",
    "3連単", "3連単人気",
    # 補正系（結果由来）
    "補正", "補9", "基準タイム",
    # 馬体重（当日計量 — 使いたい場合もあるが安全側で除外）
    "馬体重", "馬体重増減",
    # 異常コード（結果）
    "異常コード",
    # コメント系（レース後）
    "結果コメント", "結果コメントS",
    "レースコメント", "レースコメントS",
    # ID系（モデルに無意味）
    "M", "データ順番号", "レースID(新)", "レースID(新/馬番無)", "レースID(旧)",
    "血統登録番号", "騎手コード", "調教師コード",
    "前走レースID(新)", "前走レースID(新/馬番無)", "前走レースID(旧)",
    "前走騎手コード",
    # テキスト系（そのままでは使えない）
    "レース名", "馬名", "馬名S", "騎手", "騎手.1", "調教師", "調教師.1",
    "馬主(最新/仮想)", "馬主(レース時)", "生産者", "種牡馬", "母馬", "母父馬",
    "予想コメント", "予想コメントS",
    "KOL関係者コメント", "KOLコメントS", "KOL次走へのメモ", "KOL次走メモS",
    "ワーク1", "ワーク1S", "ワーク2", "ワーク2S",
    "チェック馬コメントS", "チェック日",
    "前走レース名", "前走結果コメントS", "前走予想コメントS",
    "前走レースコメントS", "前走KOL関係者コメントS", "前走KOL次走へのメモS",
    "前走ワーク1", "前走ワーク1S", "前走ワーク2", "前走ワーク2S",
    "前騎手", "前騎手.1", "前走調教師",
    # 矢印系（不明カラム）
    "→", "←",
]

# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def load_raw_data() -> pd.DataFrame:
    """CSVを読み込み、基本的な前処理を行う"""
    df = pd.read_csv(DATA_PATH, encoding="utf-8", low_memory=False).copy()

    # 日付を正規化してdatetime化
    date_str = df["日付(yyyy.mm.dd)"].str.replace(r"\s+", "", regex=True)
    df["date"] = pd.to_datetime(date_str, format="%Y.%m.%d")

    # レースIDの作成（日付+場所+R）
    df["race_id"] = (
        df["date"].dt.strftime("%Y%m%d")
        + "_" + df["場所"].astype(str)
        + "_R" + df["R"].astype(str)
    )

    # 着順を数値化（'止','外','消'などは NaN）
    df["finish"] = pd.to_numeric(df["着順"], errors="coerce")

    # 勝ちフラグ（1着=1, それ以外=0, NaN=0）
    df["win"] = (df["finish"] == 1).astype(int)

    # 単勝オッズ（NaN除去）
    df["odds"] = pd.to_numeric(df["単勝オッズ"], errors="coerce")

    # 単勝配当を数値化（勝ち馬のみ数値、負け馬は括弧つきなので0）
    payout_col = df["単勝配当"].astype(str).str.strip()
    is_winner = ~payout_col.str.startswith("(") & (payout_col != "nan")
    df["payout"] = pd.to_numeric(payout_col.where(is_winner, "0"), errors="coerce").fillna(0).astype(int)

    # 異常レコードを除外（取消・中止・除外）
    invalid_finishes = df["着順"].isin(["止", "外", "消"])
    abnormal_codes = df["異常コード"].isin([1, 3, 4]) if "異常コード" in df.columns else pd.Series(False, index=df.index)
    df = df[~(invalid_finishes | abnormal_codes)].copy()

    # オッズ欠損を除外
    df = df.dropna(subset=["odds"]).copy()

    return df


def split_train_test(df: pd.DataFrame):
    """時系列で学習/テストに分割"""
    train_end = pd.to_datetime(TRAIN_END.replace(" ", ""), format="%Y.%m.%d")
    test_start = pd.to_datetime(TEST_START.replace(" ", ""), format="%Y.%m.%d")

    train = df[df["date"] <= train_end].copy()
    test = df[df["date"] >= test_start].copy()

    return train, test


def get_feature_columns(df: pd.DataFrame) -> list:
    """データリークのないカラムのみ返す（数値カラムのみ）"""
    exclude = set(LEAKAGE_COLUMNS)
    # 内部で作成したカラムも除外
    exclude.update(["date", "race_id", "finish", "win", "odds", "payout",
                     "日付(yyyy.mm.dd)", "日付", "日付S"])

    candidates = []
    for col in df.columns:
        if col in exclude:
            continue
        if df[col].dtype in [np.float64, np.int64, np.float32, np.int32]:
            candidates.append(col)
    return candidates


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@dataclass
class ExperimentResult:
    """1実験の結果"""
    roi: float                  # 回収率 (%)
    hit_rate: float             # 的中率 (%)
    total_bets: int             # 総賭けレース数
    annual_bets: float          # 年間平均賭けレース数
    sharpe_ratio: float         # シャープレシオ（月次回収率ベース）
    total_return: int           # 総回収額
    total_wagered: int          # 総投資額（100円 × 賭けレース数）
    profit: int                 # 損益
    constraints_met: bool       # 制約条件を満たしているか
    monthly_roi: dict = field(default_factory=dict)  # 月別回収率


def evaluate(df_test: pd.DataFrame, bet_mask: np.ndarray) -> ExperimentResult:
    """
    テストデータと賭けマスク（True=賭ける）から評価指標を計算する。
    均等買い単勝100円として計算。

    Args:
        df_test: テスト期間のDataFrame
        bet_mask: boolean配列、len(df_test)と同じ長さ

    Returns:
        ExperimentResult
    """
    assert len(bet_mask) == len(df_test), f"bet_mask length mismatch: {len(bet_mask)} vs {len(df_test)}"

    bets = df_test[bet_mask].copy()
    total_bets = len(bets)

    if total_bets == 0:
        return ExperimentResult(
            roi=0.0, hit_rate=0.0, total_bets=0, annual_bets=0.0,
            sharpe_ratio=0.0, total_return=0, total_wagered=0, profit=0,
            constraints_met=False, monthly_roi={},
        )

    # 基本指標
    total_wagered = total_bets * 100  # 100円均等買い
    total_return = bets["payout"].sum()
    profit = total_return - total_wagered
    roi = (total_return / total_wagered) * 100
    hits = (bets["win"] == 1).sum()
    hit_rate = hits / total_bets * 100

    # 年間賭けレース数
    date_range_days = (bets["date"].max() - bets["date"].min()).days + 1
    years = max(date_range_days / 365.25, 0.1)
    annual_bets = total_bets / years

    # 月別回収率 → シャープレシオ
    bets["year_month"] = bets["date"].dt.to_period("M")
    monthly = bets.groupby("year_month").agg(
        wagered=("win", "count"),
        returned=("payout", "sum"),
    )
    monthly["roi"] = monthly["returned"] / (monthly["wagered"] * 100) * 100
    monthly_roi_dict = {str(k): round(v, 2) for k, v in monthly["roi"].items()}

    # シャープレシオ（月次回収率の平均と標準偏差から）
    monthly_returns = monthly["roi"].values - 100  # 超過リターン（%）
    if len(monthly_returns) > 1 and np.std(monthly_returns) > 0:
        sharpe = np.mean(monthly_returns) / np.std(monthly_returns) * math.sqrt(12)
    else:
        sharpe = 0.0

    # 制約チェック
    constraints_met = (hit_rate >= MIN_HIT_RATE * 100) and (annual_bets >= MIN_ANNUAL_BETS)

    return ExperimentResult(
        roi=round(roi, 2),
        hit_rate=round(hit_rate, 2),
        total_bets=total_bets,
        annual_bets=round(annual_bets, 1),
        sharpe_ratio=round(sharpe, 3),
        total_return=int(total_return),
        total_wagered=total_wagered,
        profit=profit,
        constraints_met=constraints_met,
        monthly_roi=monthly_roi_dict,
    )


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

def plot_results(results_tsv_path: str, output_path: str = "progress.png"):
    """results.tsv を読み込んで可視化する"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import rcParams

    # 日本語フォント設定（macOS）
    for font in ["Hiragino Sans", "Hiragino Kaku Gothic Pro", "Yu Gothic", "Meiryo"]:
        try:
            rcParams["font.family"] = font
            break
        except:
            pass
    rcParams["axes.unicode_minus"] = False

    if not os.path.exists(results_tsv_path):
        print(f"No results file found: {results_tsv_path}")
        return

    df = pd.read_csv(results_tsv_path, sep="\t")
    if len(df) == 0:
        return

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle("競馬回収率改善ループ — 実験結果", fontsize=16, fontweight="bold")

    # --- 1. 回収率の推移 ---
    ax = axes[0, 0]
    colors = ["green" if s == "keep" else "gray" if s == "discard" else "red"
              for s in df["status"]]
    ax.bar(range(len(df)), df["roi"], color=colors, alpha=0.7)
    ax.axhline(y=100, color="red", linestyle="--", linewidth=1.5, label="損益分岐点 (100%)")
    ax.set_xlabel("実験番号")
    ax.set_ylabel("回収率 (%)")
    ax.set_title("回収率の推移")
    ax.legend()

    # --- 2. 一覧表 ---
    ax = axes[0, 1]
    ax.axis("off")
    table_data = []
    for _, row in df.iterrows():
        table_data.append([
            row.get("experiment", ""),
            f"{row['roi']:.1f}%",
            f"{row['hit_rate']:.1f}%",
            str(int(row["total_bets"])),
            f"{row['sharpe']:.2f}",
            row["status"],
        ])
    if table_data:
        table = ax.table(
            cellText=table_data,
            colLabels=["実験", "回収率", "的中率", "賭け数", "Sharpe", "状態"],
            loc="center",
            cellLoc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1.0, 1.5)
        for i, row in enumerate(df.itertuples()):
            color = "#d4edda" if row.status == "keep" else "#f8d7da" if row.status == "crash" else "#fff3cd"
            for j in range(6):
                table[i + 1, j].set_facecolor(color)
    ax.set_title("実験一覧")

    # --- 3. 最良モデルの月別回収率 ---
    ax = axes[1, 0]
    if "monthly_roi_json" in df.columns:
        # keep かつ最良 ROI の行
        keeps = df[df["status"] == "keep"]
        if len(keeps) > 0:
            best = keeps.loc[keeps["roi"].idxmax()]
            try:
                monthly = json.loads(best["monthly_roi_json"])
                months = sorted(monthly.keys())
                values = [monthly[m] for m in months]
                bar_colors = ["green" if v >= 100 else "red" for v in values]
                ax.bar(range(len(months)), values, color=bar_colors, alpha=0.7)
                ax.set_xticks(range(len(months)))
                ax.set_xticklabels(months, rotation=45, ha="right", fontsize=7)
                ax.axhline(y=100, color="red", linestyle="--", linewidth=1)
                ax.set_ylabel("回収率 (%)")
                ax.set_title(f"最良モデル({best['experiment']})の月別回収率")
            except (json.JSONDecodeError, KeyError):
                ax.text(0.5, 0.5, "月別データなし", ha="center", va="center", transform=ax.transAxes)
                ax.set_title("月別回収率")
        else:
            ax.text(0.5, 0.5, "keepされた実験なし", ha="center", va="center", transform=ax.transAxes)
            ax.set_title("月別回収率")
    else:
        ax.text(0.5, 0.5, "月別データなし", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("月別回収率")

    # --- 4. 回収率 vs シャープレシオ ---
    ax = axes[1, 1]
    if len(df) > 0 and "sharpe" in df.columns:
        for _, row in df.iterrows():
            color = "green" if row["status"] == "keep" else "gray" if row["status"] == "discard" else "red"
            ax.scatter(row["sharpe"], row["roi"], c=color, s=60, alpha=0.7)
            ax.annotate(str(row.get("experiment", "")), (row["sharpe"], row["roi"]), fontsize=7)
        ax.axhline(y=100, color="red", linestyle="--", linewidth=1)
        ax.axvline(x=0, color="gray", linestyle="--", linewidth=0.5)
        ax.set_xlabel("シャープレシオ")
        ax.set_ylabel("回収率 (%)")
        ax.set_title("回収率 vs シャープレシオ")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Progress chart saved to {output_path}")


# ---------------------------------------------------------------------------
# Results logging
# ---------------------------------------------------------------------------

RESULTS_HEADER = "experiment\troi\thit_rate\ttotal_bets\tannual_bets\tsharpe\tstatus\tdescription\tmonthly_roi_json\n"


def init_results(path: str):
    """results.tsv を初期化する"""
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(RESULTS_HEADER)


def log_result(path: str, experiment: str, result: ExperimentResult, status: str, description: str):
    """results.tsv に1行追記する"""
    monthly_json = json.dumps(result.monthly_roi, ensure_ascii=False)
    line = (
        f"{experiment}\t{result.roi}\t{result.hit_rate}\t{result.total_bets}\t"
        f"{result.annual_bets}\t{result.sharpe_ratio}\t{status}\t{description}\t{monthly_json}\n"
    )
    with open(path, "a") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------------------

def print_summary(result: ExperimentResult):
    """実験結果のサマリーを表示"""
    print("---")
    print(f"roi:              {result.roi:.2f}%")
    print(f"hit_rate:         {result.hit_rate:.2f}%")
    print(f"total_bets:       {result.total_bets}")
    print(f"annual_bets:      {result.annual_bets:.1f}")
    print(f"sharpe_ratio:     {result.sharpe_ratio:.3f}")
    print(f"total_return:     {result.total_return:,}")
    print(f"total_wagered:    {result.total_wagered:,}")
    print(f"profit:           {result.profit:+,}")
    print(f"constraints_met:  {result.constraints_met}")

    # 制約詳細
    if result.hit_rate < MIN_HIT_RATE * 100:
        print(f"  WARNING: hit_rate {result.hit_rate:.2f}% < {MIN_HIT_RATE*100:.0f}%")
    if result.annual_bets < MIN_ANNUAL_BETS:
        print(f"  WARNING: annual_bets {result.annual_bets:.1f} < {MIN_ANNUAL_BETS}")
