// 2026-05-23 picks.json の構造を模倣したモックデータ
// 開発時 / R2 fetch 失敗時のフォールバック

export const mockPicks = {
  generated_at: '2026-05-23T01:01:25',
  date_from: '2026-05-23',
  date_to: '2026-05-23',
  merged: {
    current_cap: 10000,
    pct: 0.0717,
    races: [
      {
        race_id: '202605020911',
        date: '2026-05-23',
        picks: [6],
        race_name: '欅S',
        venue: '東京',
        race_num: 11,
        surface: 'ダ',
        distance: 1400,
        horse_names: { 6: 'シンバーシア' },
        bet_per: 700,
        total: 700,
      },
    ],
    total_wagered: 700,
  },
  dup: {
    current_cap: 10000,
    pct: 0.081,
    races: [
      {
        strategy: 'ｵｰﾌﾟﾝ 血+休+P',
        race_id: '202605020911',
        date: '2026-05-23',
        picks: [6],
        race_name: '欅S',
        venue: '東京',
        race_num: 11,
        surface: 'ダ',
        distance: 1400,
        horse_names: { 6: 'シンバーシア' },
        bet_per: 800,
        total: 800,
      },
      {
        strategy: 'ｵｰﾌﾟﾝ 血+齢+昇+P',
        race_id: '202605020911',
        date: '2026-05-23',
        picks: [6],
        race_name: '欅S',
        venue: '東京',
        race_num: 11,
        surface: 'ダ',
        distance: 1400,
        horse_names: { 6: 'シンバーシア' },
        bet_per: 800,
        total: 800,
      },
    ],
    total_wagered: 1600,
  },
}

export const mockPortfolio = {
  current_cap: 10000,
  initial_cap: 10000,
  total_roi_pct: 0.0,
  total_bets: 0,
  total_hits: 0,
  history: [],
}
