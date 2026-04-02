# keiba_loop_trifecta

競馬三連単の実運用耐久性を最大化する自律実験ループ。

## Files

- **`prepare.py`** — 固定。データ読み込み・シャッフル・評価・可視化。変更しない。
- **`train.py`** — 唯一編集するファイル。特徴量・モデル・賭け戦略のすべてがここにある。
- **`Data_2020_fixed.csv`** — 元データ。CSVは着順順に並んでいるがprepare.pyでシャッフル済み。

## Goal

**主指標: Sortino ratio（月次回収率ベース）を最大化する。**

Sharpeではなく Sortino を使う理由: Sharpe は上振れ（大勝ち月）もリスクとしてカウントする。
競馬のように「たまに大きく当たる」構造では、下振れだけを測る Sortino の方が実態に合う。

副指標（重要度順）:
1. **MDD（最大ドローダウン）** — ピーク利益からの最大下落率。低いほど良い。目標: 30%以下
2. **年間利益額（円）** — ROIが高くてもレース数が少なければ意味がない。回転率を考慮した絶対額
3. **最大連続赤字月** — 実運用の心理的耐久性。目標: 3以下
4. **破産確率** — ケリー基準ベース。100単位開始で実質0であること
5. **ROI（回収率%）** — 100%超がプラス収支

制約:
- ROI >= 100%（赤字は絶対NG）
- 破産確率(100単位) < 0.01（これを超えるなら実運用不可）
- 実運用前提 — バックテスト過学習に注意。テスト期間は2024-01〜2025-10。

## Evaluation metrics（train.pyが出力する）

```
roi:              163.61%      # 回収率
hit_rate:         6.04%        # 的中率
sharpe_ratio:     1.773        # シャープレシオ（参考値）
sortino:          3.851        # ソルティノレシオ（主指標）
mdd_pct:          15.5%        # 最大ドローダウン（対ピーク%）
mdd_yen:          35,828円     # 最大ドローダウン（円）
max_losing_streak: 4           # 最大連続赤字月
win_lose_months:  15/7         # 黒字月/赤字月
kelly_fraction:   2.44%        # ケリー推奨賭け比率
ruin_prob_100:    0.000000     # 破産確率(100単位開始)
annual_profit:    +122,818円   # 年間想定利益
annual_bets:      321.8        # 年間レース数
```

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
grep "^roi:\|^hit_rate:\|^sharpe_ratio:\|^sortino:\|^mdd_pct:\|^max_losing_streak:\|^annual_profit:\|^ruin_prob:" run.log
```

1実験あたり約25秒。

## Judgment criteria

**keep** の条件（すべて満たす）:
- Sortino ratio が現在のベストを上回る
- ROI >= 100%（プラス収支）
- MDD が極端に悪化していない（目安: 現ベストの1.5倍以内）
- 破産確率(100単位) < 0.01

**keep（強い）**:
- Sortino改善かつMDD改善（両方良くなるのが最良）

**keep（弱い）**:
- Sortino改善だがMDD微悪化（トレードオフが許容範囲内）

**discard** の条件:
- Sortino低下、またはROI 100%割れ、またはMDDが大幅悪化

keep時は `git commit` して進む。discard時は `git checkout train.py` で戻す。

## Logging

results.tsvに毎回記録する（git管理外）:

```
experiment	roi	hit_rate	total_bets_races	total_bets_combos	annual_bets	avg_combos	sharpe	sortino	mdd_pct	max_lose_streak	annual_profit	status	description	monthly_roi_json
```

## Current state

**ベスト: exp32e (Sortino 3.851, Sharpe 1.773)**
- ROI 163.61%, 的中率 6.04%, 563R, 利益+214,860円
- MDD 15.5%（35,828円）、最大連続赤字月 4、黒字月 15/22
- Kelly 2.44%、破産確率 ~0
- MIN_TOP1_ODDS=3.8, 全レースTOP3ボックス6点, MIN_RACE_RUNNERS=12
- 特徴量: 単勝オッズ,人気,複勝シェア,複勝オッズ下限 + 前走系 + コース/馬属性
- モデル: LightGBM binary(3着以内), num_leaves=100, lr=0.10

## Known findings

### 効いたこと
- 全レースTOP3ボックス6点（動的2/6→固定6）: 的中率倍増、Sortino大幅改善
- MIN_TOP1_ODDS引き上げ（3.3→3.8）: 荒れるレースに集中でROI・安定性改善
- 複勝市場の特徴量（複勝シェア、複勝オッズ下限）: 単勝オッズと補完的な情報
- 人気（順位）追加: オッズ値とは別のシグナル
- num_leaves=100: 表現力と正則化のバランス

### 効かなかったこと
- 特徴量一括追加: ノイズで悪化（1つずつ厳選追加が必要）
- multi-seed平均: 予測が平滑化されすぎて有害
- LambdaRank: binary classificationに劣る
- 正則化強化（lambda, min_child_samples変更）: 現設定が最適
- lr変更（0.08, 0.12）: lr=0.10がスイートスポット
- feature_fraction変更: 0.8がベスト
- 2モデルブレンド(top3+win): 単モデルに劣る
- target=2着以内: データ不足
- target=1着のみ: データ不足（過去知見）
- 血統タイプ・天気・コース区分: ノイズ
- 複勝オッズ幅/上限: 有害
- 複勝人気: 有害
- 前走複勝シェア・オッズ下限: ノイズ
- 前走人気: ノイズ
- 斤量体重比: 馬体重由来でリーク的
- 単複比: ノイズ
- オッズ変動率: NaN多で無効
- 多頭出し: データ欠損で無効
- MIN_RACE_RUNNERS=14: レース減りすぎ
- VOLATILE_THRESHOLD変更(3.5, 4.5): 4.0が最適→全ボックスで不要に

## Exploration ideas（未試行）

### 特徴量
- 馬印・レース印（専門紙の予想マーク — オッズと異なるシグナル源）
- 騎手・調教師の過去成績集約特徴量（コード自体はリーク列だが、成績統計は使える可能性）
- 前走距離との差（距離変更の影響）
- レース内のオッズ分布特徴量（標準偏差、歪度 — レースの荒れ度合い）
- 枠番×芝ダ（内枠有利/外枠有利がコース種別で異なる）

### 賭け戦略
- 月間の損失上限を設けるストップロス
- 配当期待値ベースの賭け選択（予測スコア×推定配当）
- レース内の予測スコア分布に基づく動的フィルタ
- 高配当月に賭け金を増やすマルチンゲール的戦略（破産確率とのトレードオフ注意）

### モデル
- 時系列クロスバリデーション（現在は単純なtrain/val分割）
- Validation期間を2023年だけにする（直近のパターンに合わせる）

## NEVER STOP

ループを開始したら、手動で止められるまで永遠に回し続ける。
「続けていいですか？」と聞かない。アイデアが尽きたら、組み合わせを試す、
既存の知見を再検討する、より大胆な変更を試す。
