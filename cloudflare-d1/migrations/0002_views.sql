-- VIEW 定義 (entries の finish=1 行をレース単位に切り出す軽量ビュー)
-- ※ D1 は VIEW を CREATE VIEW でサポート

CREATE VIEW IF NOT EXISTS races AS
SELECT DISTINCT
    race_id, date, year, venue, race_num, race_name,
    class, surface, distance, condition, weather,
    runners, win_payout, trifecta
FROM entries
WHERE finish = 1;
