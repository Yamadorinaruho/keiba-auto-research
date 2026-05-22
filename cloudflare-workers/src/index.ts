/**
 * keiba-api — Cloudflare Workers + Hono.js port of keiba-dashboard/api.py.
 *
 * This file is currently a routing skeleton: every endpoint that exists in
 * the FastAPI app is wired up to a handler stub. Implementation bodies are
 * intentionally left as TODO comments — see README.md for the migration plan
 * and priority order.
 *
 * Paths and HTTP methods MUST match the FastAPI decorators exactly so the
 * frontend keiba-dashboard can switch over without changes.
 */

import { Hono } from "hono";
import { cors } from "hono/cors";
import type { Bindings } from "./types";

const app = new Hono<{ Bindings: Bindings }>();

// ---------------------------------------------------------------------------
// CORS — mirrors FastAPI add_middleware(CORSMiddleware, allow_origins=["*"]).
// ---------------------------------------------------------------------------
app.use(
  "*",
  cors({
    origin: "*",
    allowMethods: ["GET", "POST", "OPTIONS"],
    allowHeaders: ["*"],
    credentials: true,
  }),
);

// Health check (not in FastAPI, but useful for `wrangler dev`).
app.get("/", (c) => c.json({ ok: true, service: "keiba-api" }));

// ---------------------------------------------------------------------------
// 1. GET /api/meta
//    FastAPI: get_meta()
//    Returns distinct venues/classes/surfaces/conditions/weathers/genders/
//    years/distances + race_names_graded + total_rows.
// ---------------------------------------------------------------------------
app.get("/api/meta", async (c) => {
  // TODO: run 9 DISTINCT-column queries + 1 COUNT(*) against KEIBA_DB and
  //       cache the result in a module-scoped variable (FastAPI used
  //       _meta_cache). Consider Cache API for cross-isolate persistence.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 2. GET /api/summary
//    FastAPI: get_summary(venue, surface, class_, condition, year, gender)
//    Returns aggregate stats (total_races, fav_win_rate, avg_trifecta, etc.).
// ---------------------------------------------------------------------------
app.get("/api/summary", async (c) => {
  // TODO: parse 6 optional comma-separated multi-filters, build WHERE clause,
  //       run single aggregate query, post-process with rnd/pct helpers.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 3. GET /api/roi-heatmap  (alias: /api/accuracy-heatmap)
//    FastAPI: get_accuracy_heatmap(row_axis, col_axis, pop_min, pop_max, ...)
//    2D grouping with row/col/grand totals + ROI calc.
// ---------------------------------------------------------------------------
const heatmapHandler = async (c: Parameters<Parameters<typeof app.get>[1]>[0]) => {
  // TODO: GROUP BY two axes (chosen from AXIS_SQL), compute row_totals /
  //       col_totals / grand_total in JS post-aggregation pass. Distance-band
  //       CASE expression must be ported verbatim from distance_band_sql().
  return c.json({ error: "not implemented" }, 501);
};
app.get("/api/roi-heatmap", heatmapHandler);
app.get("/api/accuracy-heatmap", heatmapHandler);

// ---------------------------------------------------------------------------
// 4. GET /api/rotation
//    FastAPI: get_rotation(race_name)
//    Per-race rotation analytics: prev_races stats + prev_finish detail.
// ---------------------------------------------------------------------------
app.get("/api/rotation", async (c) => {
  // TODO: 4 sub-queries (summary, top_prev, prev_races, prev_finish_detail),
  //       all keyed on race_name=?. Convert AVG(boolean) ratios via pct().
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 5. GET /api/rotation-condition
//    FastAPI: get_rotation_condition(venue, surface, distance, class_)
//    Same shape as /rotation but filters by race conditions instead of name.
// ---------------------------------------------------------------------------
app.get("/api/rotation-condition", async (c) => {
  // TODO: identical structure to /api/rotation but uses build_where() on
  //       (venue, surface, distance, class). Reuse rotation helper once
  //       implemented.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 6. GET /api/profit-scan  (alias: /api/strength-scan)
//    FastAPI: get_strength_scan(min_n, min_place_rate, ...filters)
//    UNION-ALL of 11 condition groupings + Wilson-ish CI lower bound.
// ---------------------------------------------------------------------------
const strengthScanHandler = async (
  c: Parameters<Parameters<typeof app.get>[1]>[0],
) => {
  // TODO: build the 11 SELECT blocks (venue / surface / distance_band /
  //       condition / popularity / cross combos / jockey / sire), UNION ALL
  //       them, then filter by min_n + min_place_rate in JS. Compute 95% CI
  //       lower bound: p - 1.96 * sqrt(p(1-p)/n).
  return c.json({ error: "not implemented" }, 501);
};
app.get("/api/profit-scan", strengthScanHandler);
app.get("/api/strength-scan", strengthScanHandler);

// ---------------------------------------------------------------------------
// 7. GET /api/upset-ranking
//    FastAPI: get_upset_ranking(...filters)
//    3 groupings (venue / surface×distance_band / class), upset_score formula.
// ---------------------------------------------------------------------------
app.get("/api/upset-ranking", async (c) => {
  // TODO: loop over 3 GROUP BY expressions, compute
  //       (1 - fav_win_rate) * avg_win_payout / 1000 as upset_score,
  //       HAVING race_count >= 30. Sort asc + desc, take top-20 each.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 8. GET /api/search
//    FastAPI: search_entries(type, q)
//    LIKE-based search over horse / jockey / sire / trainer + recent records.
// ---------------------------------------------------------------------------
app.get("/api/search", async (c) => {
  // TODO: map type -> column (horse/jockey/sire/trainer), run stats query +
  //       recent-50 records query (ORDER BY date DESC, race_num DESC).
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 9. GET /api/entries
//    FastAPI: list_entries(page, per_page, sort, order, q, ...filters)
//    Paginated entries listing with multi-column free-text search.
// ---------------------------------------------------------------------------
app.get("/api/entries", async (c) => {
  // TODO: whitelist sort column against SORTABLE_COLS, build WHERE +
  //       optional LIKE OR-block for q, run COUNT(*) + data query with
  //       LIMIT/OFFSET. Tie-break ORDER BY race_num + number.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 10. GET /api/condition-strength
//     FastAPI: get_condition_strength(jockey, sire, trainer, venue, surface, distance)
//     Overall stats + per-class breakdown for arbitrary filter combo.
// ---------------------------------------------------------------------------
app.get("/api/condition-strength", async (c) => {
  // TODO: require at least one filter set; run aggregate stats + by_class
  //       GROUP BY query. Return empty stats shape if n=0.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 11. GET /api/horse-form
//     FastAPI: get_horse_form(horse)
//     Overall stats + recent 20 races + surface×distance_band breakdown.
// ---------------------------------------------------------------------------
app.get("/api/horse-form", async (c) => {
  // TODO: 3 queries keyed on horse=? — stats, recent 20, condition breakdown.
  //       Distance-band CASE expression reused from helper.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 12. GET /api/race-entries
//     FastAPI: get_race_entries(race_name, year?)
//     Fetches a specific race's horses; if year omitted, uses latest.
// ---------------------------------------------------------------------------
app.get("/api/race-entries", async (c) => {
  // TODO: 1) DISTINCT years for race_name LIKE ?; 2) fetch entries for
  //       (race_name, year); 3) shape into race-level + horses[] payload.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 13. POST /api/race-analysis
//     FastAPI: race_analysis(RaceAnalysisRequest)
//     Composite scoring (rotation/sire/jockey/horse) + factor flags per horse.
// ---------------------------------------------------------------------------
app.post("/api/race-analysis", async (c) => {
  // TODO: parse JSON body, validate with RaceAnalysisRequest shape (consider
  //       zod or hono/validator). For each horse run 4 stats queries (with
  //       LIKE patterns) and compute composite_score with weights
  //       0.35 rotation + 0.20 sire + 0.20 jockey + 0.25 horse_form.
  //       Sort horses[] by composite_score desc.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 14. GET /api/backtest-g1
//     FastAPI: backtest_g1()
//     Walk-forward composite-score backtest over all G1 races with
//     date_cutoff to prevent leakage. Compute top1/top3/top5 hits + ROI.
// ---------------------------------------------------------------------------
app.get("/api/backtest-g1", async (c) => {
  // TODO: iterate G1 races; per race compute date-cutoff composite for each
  //       horse (4 sub-queries × N horses — expensive!). Accumulate hit
  //       counters + ROI buckets (top1_win / top3_place / trio_box).
  //       NOTE: large N+1 SQL fan-out — consider precomputing offline and
  //       serving from KV/R2 instead of running live in Workers.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 15. GET /api/strategies
//     FastAPI: get_strategies()
//     Returns precomputed strategy_results.json contents.
// ---------------------------------------------------------------------------
app.get("/api/strategies", async (c) => {
  // TODO: serve strategy_results.json from R2 / KV / asset bundle.
  //       Workers cannot read arbitrary local filesystem like FastAPI did.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 16. GET /api/wealth-curve
//     FastAPI: get_wealth_curve()
//     Returns precomputed wealth_data.json (~4MB).
// ---------------------------------------------------------------------------
app.get("/api/wealth-curve", async (c) => {
  // TODO: serve wealth_data.json from R2 (size > 1MB CPU limit if inlined).
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 17. GET /api/edge-validation
//     FastAPI: get_edge_validation()
//     Returns precomputed edge_data.json.
// ---------------------------------------------------------------------------
app.get("/api/edge-validation", async (c) => {
  // TODO: serve edge_data.json from R2 / KV / asset bundle.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 18. GET /api/analytics
//     FastAPI: get_analytics()
//     Returns precomputed analytics_data.json (walk-forward / yearly / etc.).
// ---------------------------------------------------------------------------
app.get("/api/analytics", async (c) => {
  // TODO: serve analytics_data.json from R2 / KV / asset bundle.
  return c.json({ error: "not implemented" }, 501);
});

// ---------------------------------------------------------------------------
// 19. GET /api/live-state
//     FastAPI: get_live_state()
//     Returns state/portfolio.json + most recent state/picks/*.json.
// ---------------------------------------------------------------------------
app.get("/api/live-state", async (c) => {
  // TODO: needs writeable storage for daily-updated picks. Recommend R2
  //       bucket with `picks/YYYY-MM-DD.json` keys + a single
  //       `portfolio.json` object. Cron Trigger or external uploader
  //       updates these; Worker just reads.
  return c.json({ error: "not implemented" }, 501);
});

// 404 fallback
app.notFound((c) => c.json({ error: "not found" }, 404));

export default app;
