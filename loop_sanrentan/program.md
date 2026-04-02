# keiba_loop_trifecta

競馬三連単のシャープレシオ最大化を目指す自律実験ループ。

## Files

- **`prepare.py`** — 固定。データ読み込み・シャッフル・評価・可視化。変更しない。
- **`train.py`** — 唯一編集するファイル。特徴量・モデル・賭け戦略のすべてがここにある。
- **`Data_2020_fixed.csv`** — 元データ。CSVは着順順に並んでいるがprepare.pyでシャッフル済み。

## Goal

**主指標: Sharpe ratio（月次回収率ベース）を最大化する。**

副指標（これらも高いほど良い）:
- ROI（回収率%）— 100%超がプラス収支
- 的中率 — 高いほど安定（ただし点数増はコスト増を伴う）

制約:
- 年間レース数 >= 600（目安。Sharpeが十分高ければ多少下回ってもOK）
- 実運用前提 — バックテスト過学習に注意。テスト期間は2024-01〜2025-10。

## What you CAN edit

`train.py` のみ。具体的には:

1. **特徴量** (`engineer_features` + `feature_cols`) — 最優先の探索軸
2. **賭け戦略** (`select_trifecta_bets`) — 次に重要
3. **モデル** (`LGBM_PARAMS`, 学習方法) — 三番目

大胆に変えてよい。一度に複数の変更をしてもよい。

## What you CANNOT do

- `prepare.py` を変更する
- パッケージを追加する
- CSVデータを変更する

## Execution

```bash
uv run python train.py > run.log 2>&1
grep "^roi:\|^hit_rate:\|^sharpe_ratio:\|^annual_bets:\|^profit:\|^constraints_met:" run.log
```

1実験あたり約25秒。

## Judgment criteria

**keep** の条件（すべて満たす）:
- Sharpe ratio が現在のベストを上回る
- ROI >= 100%（プラス収支）
- 年間レース数が極端に少なくない（目安600以上）

**discard** の条件:
- Sharpe低下、またはROI 100%割れ

keep時は `git commit` して進む。discard時は `git checkout train.py` で戻す。

## Logging

results.tsvに毎回記録する（git管理外）:

```
experiment	roi	hit_rate	total_bets_races	total_bets_combos	annual_bets	avg_combos	sharpe	status	description	monthly_roi_json
```

## Current state

**ベスト: baseline (Sharpe 1.228)**
- ROI 143.34%, 的中率 3.24%, 1203R, 利益+170,660円
- MIN_TOP1_ODDS=3.3, 動的点数(荒れ→6点ボックス/堅め→2点), MIN_RACE_RUNNERS=12
- 黒字月 13/22, 赤字月 9/22 (0%月が3つ)

## Known findings (リーク修正後)

### 効いたこと
- MIN_TOP1_ODDS引き上げ（3.0→3.3）: 荒れるレースに絞るとROI・Sharpe改善
- 動的点数（荒れ→ボックス、堅め→1着固定）: 固定点数より効率的

### 効かなかったこと
- num_leaves削減（10）: 過正則化で悪化
- bagging_fraction削減（0.6）: 同上
- 点数増加（6点固定）: コスト負けでROI 65%
- オッズ除外: ROI 100%割れ（オッズは必須特徴量）
- lr=0.05: 微低下
- MIN_RACE_RUNNERS=10: レース増えるがROI低下
- target=1着のみ: データ不足で大幅悪化

### 特徴量の現状
単勝オッズがgainの83%を占めている。モデルはほぼオッズだけで予測。
オッズ以外の特徴量の情報量を増やすことが最も改善余地が大きい。

## Exploration ideas（未試行）

### 特徴量（最優先）
- 時系オッズの変動特徴量（既にengineerしてあるが feature_cols に入ってない）
- 血統タイプコード（父タイプ名_code, 母父タイプ名_code — 既にengineer済み未使用）
- コース区分_code, 所属_code, 天気_code（engineer済み未使用）
- レース内オッズ順位（人気順位）
- レース内の相対特徴量（オッズのレース内偏差値など）

### 賭け戦略
- 予測スコアの信頼度フィルタ（TOP1とTOP2のスコア差が大きいレースだけ賭ける）
- 配当期待値ベースの賭け選択
- 月間の赤字を減らすための分散戦略

### モデル
- LambdaRank（レース内ランキング学習）
- 複数seed平均（予測の安定化）
- 時系列クロスバリデーション

## NEVER STOP

ループを開始したら、手動で止められるまで永遠に回し続ける。
「続けていいですか？」と聞かない。アイデアが尽きたら、組み合わせを試す、
既存の知見を再検討する、より大胆な変更を試す。
