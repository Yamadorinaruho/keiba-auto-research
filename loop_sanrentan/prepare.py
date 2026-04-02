"""
競馬三連単回収率改善ループ — データ読み込み・評価・可視化（固定ファイル）
このファイルは変更しない。train.py のみを編集する。

Usage: train.py から import して使う
"""

import os
import json
import math
import warnings
from dataclasses import dataclass, asdict, field
from typing import Optional
from itertools import permutations

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
    # 馬体重（当日計量 — 安全側で除外）
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
    df["finish"] = pd.to_numeric(df["確定着順"], errors="coerce")

    # 単勝オッズ（NaN除去用）
    df["odds"] = pd.to_numeric(df["単勝オッズ"], errors="coerce")

    # 馬番を数値化
    df["umaban"] = pd.to_numeric(df["馬番"], errors="coerce")

    # 三連単配当（確定着順1-3の行に同じ値が入っている）
    df["trifecta_payout"] = pd.to_numeric(df["3連単"], errors="coerce").fillna(0)

    # 異常レコードを除外（取消・中止・除外: 確定着順==0）
    df = df[df["finish"] > 0].copy()

    # オッズ欠損を除外
    df = df.dropna(subset=["odds"]).copy()

    # レース内の行順をシャッフル（CSVが着順ソート済みによるリーク防止）
    df = df.sample(frac=1, random_state=42).sort_values(["date", "race_id"]).reset_index(drop=True)

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
    exclude.update(["date", "race_id", "finish", "odds", "umaban",
                     "trifecta_payout", "日付(yyyy.mm.dd)", "日付", "日付S"])

    candidates = []
    for col in df.columns:
        if col in exclude:
            continue
        if df[col].dtype in [np.float64, np.int64, np.float32, np.int32]:
            candidates.append(col)
    return candidates


# ---------------------------------------------------------------------------
# Race-level trifecta result extraction
# ---------------------------------------------------------------------------

def build_race_trifecta(df: pd.DataFrame) -> dict:
    """
    各レースの三連単正解と配当を辞書で返す。

    Returns:
        dict: {race_id: {"order": (1着馬番, 2着馬番, 3着馬番), "payout": int}}
    """
    result = {}
    for race_id, group in df.groupby("race_id"):
        top3 = group[group["finish"].isin([1, 2, 3])].sort_values("finish")
        if len(top3) < 3:
            continue
        order = tuple(top3["umaban"].values[:3].astype(int))
        payout = int(top3["trifecta_payout"].max())
        if payout > 0:
            result[race_id] = {"order": order, "payout": payout}
    return result


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@dataclass
class ExperimentResult:
    """1実験の結果"""
    roi: float                  # 回収率 (%)
    hit_rate: float             # 的中率 (%)
    total_bets_races: int       # 賭けたレース数
    total_bets_combos: int      # 賭けた組み合わせ数（=購入点数）
    annual_bets: float          # 年間平均賭けレース数
    sharpe_ratio: float         # シャープレシオ（月次回収率ベース）
    total_return: int           # 総回収額
    total_wagered: int          # 総投資額（100円 × 購入点数）
    profit: int                 # 損益
    constraints_met: bool       # 制約条件を満たしているか
    avg_combos_per_race: float  # レースあたり平均購入点数
    monthly_roi: dict = field(default_factory=dict)  # 月別回収率


def evaluate(
    df_test: pd.DataFrame,
    race_bets: dict,
    race_trifecta: dict,
) -> ExperimentResult:
    """
    三連単の評価を行う。

    Args:
        df_test: テスト期間のDataFrame
        race_bets: {race_id: [(1着馬番, 2着馬番, 3着馬番), ...]} — 各レースで賭ける組み合わせ
        race_trifecta: build_race_trifecta() の返り値

    Returns:
        ExperimentResult
    """
    if not race_bets:
        return ExperimentResult(
            roi=0.0, hit_rate=0.0, total_bets_races=0, total_bets_combos=0,
            annual_bets=0.0, sharpe_ratio=0.0, total_return=0, total_wagered=0,
            profit=0, constraints_met=False, avg_combos_per_race=0.0, monthly_roi={},
        )

    # レースごとに集計
    total_combos = 0
    total_return = 0
    hits = 0
    race_results = []  # (race_id, date, wagered, returned)

    # race_id → date のマッピング
    race_dates = df_test.groupby("race_id")["date"].first().to_dict()

    for race_id, combos in race_bets.items():
        n_combos = len(combos)
        total_combos += n_combos
        wagered = n_combos * 100  # 100円/点

        returned = 0
        if race_id in race_trifecta:
            actual = race_trifecta[race_id]["order"]
            payout = race_trifecta[race_id]["payout"]
            for combo in combos:
                if tuple(combo) == actual:
                    returned = payout  # 配当値はそのまま100円あたりの払い戻し額（円）
                    hits += 1
                    break

        total_return += returned
        if race_id in race_dates:
            race_results.append((race_id, race_dates[race_id], wagered, returned))

    total_bets_races = len(race_bets)
    total_wagered = total_combos * 100

    if total_wagered == 0:
        return ExperimentResult(
            roi=0.0, hit_rate=0.0, total_bets_races=0, total_bets_combos=0,
            annual_bets=0.0, sharpe_ratio=0.0, total_return=0, total_wagered=0,
            profit=0, constraints_met=False, avg_combos_per_race=0.0, monthly_roi={},
        )

    roi = (total_return / total_wagered) * 100
    hit_rate = hits / total_bets_races * 100 if total_bets_races > 0 else 0.0
    profit = total_return - total_wagered
    avg_combos = total_combos / total_bets_races if total_bets_races > 0 else 0.0

    # 年間賭けレース数
    dates = [d for _, d, _, _ in race_results]
    if dates:
        date_range_days = (max(dates) - min(dates)).days + 1
        years = max(date_range_days / 365.25, 0.1)
        annual_bets = total_bets_races / years
    else:
        annual_bets = 0.0

    # 月別回収率 → シャープレシオ
    if race_results:
        rr_df = pd.DataFrame(race_results, columns=["race_id", "date", "wagered", "returned"])
        rr_df["year_month"] = rr_df["date"].dt.to_period("M")
        monthly = rr_df.groupby("year_month").agg(
            wagered=("wagered", "sum"),
            returned=("returned", "sum"),
        )
        monthly["roi"] = monthly["returned"] / monthly["wagered"] * 100
        monthly_roi_dict = {str(k): round(v, 2) for k, v in monthly["roi"].items()}

        monthly_returns = monthly["roi"].values - 100  # 超過リターン
        if len(monthly_returns) > 1 and np.std(monthly_returns) > 0:
            sharpe = np.mean(monthly_returns) / np.std(monthly_returns) * math.sqrt(12)
        else:
            sharpe = 0.0
    else:
        monthly_roi_dict = {}
        sharpe = 0.0

    constraints_met = annual_bets >= MIN_ANNUAL_BETS

    return ExperimentResult(
        roi=round(roi, 2),
        hit_rate=round(hit_rate, 2),
        total_bets_races=total_bets_races,
        total_bets_combos=total_combos,
        annual_bets=round(annual_bets, 1),
        sharpe_ratio=round(sharpe, 3),
        total_return=int(total_return),
        total_wagered=total_wagered,
        profit=profit,
        constraints_met=constraints_met,
        avg_combos_per_race=round(avg_combos, 1),
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
    fig.suptitle("競馬三連単回収率改善ループ — 実験結果", fontsize=16, fontweight="bold")

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
            str(int(row["total_bets_races"])),
            f"{row.get('avg_combos', 0):.1f}",
            f"{row['sharpe']:.2f}",
            row["status"],
        ])
    if table_data:
        table = ax.table(
            cellText=table_data,
            colLabels=["実験", "回収率", "的中率", "レース数", "点数/R", "Sharpe", "状態"],
            loc="center",
            cellLoc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1.0, 1.5)
        for i, row in enumerate(df.itertuples()):
            color = "#d4edda" if row.status == "keep" else "#f8d7da" if row.status == "crash" else "#fff3cd"
            for j in range(7):
                table[i + 1, j].set_facecolor(color)
    ax.set_title("実験一覧")

    # --- 3. 最良モデルの月別回収率 ---
    ax = axes[1, 0]
    if "monthly_roi_json" in df.columns:
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

RESULTS_HEADER = "experiment\troi\thit_rate\ttotal_bets_races\ttotal_bets_combos\tannual_bets\tavg_combos\tsharpe\tstatus\tdescription\tmonthly_roi_json\n"


def init_results(path: str):
    """results.tsv を初期化する"""
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(RESULTS_HEADER)


def log_result(path: str, experiment: str, result: ExperimentResult, status: str, description: str):
    """results.tsv に1行追記する"""
    monthly_json = json.dumps(result.monthly_roi, ensure_ascii=False)
    line = (
        f"{experiment}\t{result.roi}\t{result.hit_rate}\t{result.total_bets_races}\t"
        f"{result.total_bets_combos}\t{result.annual_bets}\t{result.avg_combos_per_race}\t"
        f"{result.sharpe_ratio}\t{status}\t{description}\t{monthly_json}\n"
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
    print(f"total_bets_races: {result.total_bets_races}")
    print(f"total_bets_combos:{result.total_bets_combos}")
    print(f"avg_combos/race:  {result.avg_combos_per_race:.1f}")
    print(f"annual_bets:      {result.annual_bets:.1f}")
    print(f"sharpe_ratio:     {result.sharpe_ratio:.3f}")
    print(f"total_return:     {result.total_return:,}")
    print(f"total_wagered:    {result.total_wagered:,}")
    print(f"profit:           {result.profit:+,}")
    print(f"constraints_met:  {result.constraints_met}")

    if result.annual_bets < MIN_ANNUAL_BETS:
        print(f"  WARNING: annual_bets {result.annual_bets:.1f} < {MIN_ANNUAL_BETS}")
