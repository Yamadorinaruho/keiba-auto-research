#!/usr/bin/env python3
"""
keiba.db -> Cloudflare D1 用 INSERT 分割ダンプ

entries テーブルを N行ごとの multi-row INSERT 文に分割し、
各ファイルが --max-bytes (デフォルト 90000B = 約88KB) を超えないよう
複数ステートメントに分割しつつ data/chunk_XXXX.sql に書き出す。

D1 制約:
- 1 SQL ステートメント 100KB 上限 → デフォルト 90KB で余裕を持つ
- 1 ファイル内に複数ステートメント (;区切り) は OK
- 1 ファイルあたりの上限はないが、wrangler d1 execute --file が読みやすいよう
  90KB に抑える

使い方:
    python3 scripts/dump_data.py \\
        --db ../keiba-dashboard/keiba.db \\
        --out data \\
        --rows-per-stmt 50 \\
        --max-bytes 90000
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def sql_quote(value) -> str:
    """SQLite 形式の値リテラルへ変換 (NULL / 数値 / 文字列)"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        # NaN/Inf 防御
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return "NULL"
        return repr(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    # 文字列: シングルクオートをエスケープ
    s = str(value).replace("'", "''")
    return "'" + s + "'"


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump entries to D1-compatible chunked SQL")
    parser.add_argument("--db", required=True, help="path to keiba.db")
    parser.add_argument("--out", required=True, help="output directory (e.g. data)")
    parser.add_argument("--table", default="entries", help="table name (default: entries)")
    parser.add_argument(
        "--rows-per-stmt",
        type=int,
        default=50,
        help="rows per INSERT statement (default: 50)",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=90_000,
        help="max bytes per chunk file (default: 90000 = ~88KB)",
    )
    parser.add_argument(
        "--max-stmt-bytes",
        type=int,
        default=85_000,
        help="hard cap per single INSERT statement, D1 limit is 100000. "
        "Keep < max-bytes so multiple stmts can fit per file (default: 85000)",
    )
    parser.add_argument(
        "--start-row",
        type=int,
        default=0,
        help="skip first N rows (for resume)",
    )
    parser.add_argument(
        "--chunk-start",
        type=int,
        default=1,
        help="starting chunk number (default: 1)",
    )
    parser.add_argument(
        "--since-date",
        type=str,
        default=None,
        help="filter rows by date column (YYYY-MM-DD). only rows with date >= this value are dumped",
    )
    args = parser.parse_args()

    if args.max_stmt_bytes > args.max_bytes:
        print(
            f"WARN: --max-stmt-bytes ({args.max_stmt_bytes}) > --max-bytes ({args.max_bytes}). "
            "Files may exceed max-bytes when a single stmt is too large.",
            file=sys.stderr,
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 列名取得
    cur.execute(f"PRAGMA table_info({quote_ident(args.table)})")
    cols = [r[1] for r in cur.fetchall()]
    if not cols:
        print(f"ERROR: table {args.table} not found", file=sys.stderr)
        return 1
    col_list = ", ".join(quote_ident(c) for c in cols)
    insert_prefix = f"INSERT INTO {quote_ident(args.table)} ({col_list}) VALUES "

    # WHERE 句 (since-date)
    where_clause = ""
    where_params: tuple = ()
    if args.since_date:
        where_clause = " WHERE date >= ?"
        where_params = (args.since_date,)
        print(f"filter: date >= {args.since_date}", file=sys.stderr)

    # 行数
    cur.execute(
        f"SELECT COUNT(*) FROM {quote_ident(args.table)}{where_clause}",
        where_params,
    )
    total = cur.fetchone()[0]
    print(f"total rows: {total:,}", file=sys.stderr)

    # 安定した順序で取得 (rowid 順 = 物理順)
    cur.execute(
        f"SELECT * FROM {quote_ident(args.table)}{where_clause} ORDER BY rowid",
        where_params,
    )

    chunk_no = args.chunk_start
    rows_skipped = 0
    rows_done = 0
    file_buf: list[str] = []
    file_size = 0
    stmt_rows: list[str] = []
    stmt_size = len(insert_prefix.encode("utf-8"))

    def flush_stmt():
        nonlocal stmt_rows, stmt_size, file_buf, file_size
        if not stmt_rows:
            return
        stmt = insert_prefix + ", ".join(stmt_rows) + ";\n"
        stmt_bytes = stmt.encode("utf-8")
        file_buf.append(stmt)
        file_size += len(stmt_bytes)
        stmt_rows = []
        stmt_size = len(insert_prefix.encode("utf-8"))

    def flush_file():
        nonlocal chunk_no, file_buf, file_size
        if not file_buf:
            return
        path = out_dir / f"chunk_{chunk_no:04d}.sql"
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(file_buf)
        print(f"  wrote {path} ({file_size:,} bytes, chunk={chunk_no})", file=sys.stderr)
        chunk_no += 1
        file_buf = []
        file_size = 0

    for row in cur:
        if rows_skipped < args.start_row:
            rows_skipped += 1
            continue

        values_tuple = "(" + ", ".join(sql_quote(v) for v in row) + ")"
        # +2 は ", " separator
        addition = len(values_tuple.encode("utf-8")) + 2

        # 単行が巨大すぎる場合はそれだけで1ステートメントに
        single_row_stmt_bytes = len(insert_prefix.encode("utf-8")) + len(values_tuple.encode("utf-8")) + 2
        if single_row_stmt_bytes > args.max_stmt_bytes:
            # 既存のステートメント/ファイルを flush してから単独 INSERT
            flush_stmt()
            flush_file()
            stmt_rows = [values_tuple]
            flush_stmt()
            # この単一文がファイル単体に乗らないほどデカいなら別ファイルで吐き出す
            flush_file()
            rows_done += 1
            continue

        # 現在のステートメントに追加できないなら flush
        would_be_stmt = stmt_size + addition + 2  # ";\n" 分
        if stmt_rows and (
            len(stmt_rows) >= args.rows_per_stmt
            or would_be_stmt > args.max_stmt_bytes
        ):
            flush_stmt()

        # この行を追加した状態でファイルサイズが上限を超えるなら、
        # 先に現ステートメントを flush してファイルも flush する
        # 予測ファイルサイズ = 既存 file_size + (現stmt_size or プレフィックス) + addition + ";\n"
        if stmt_rows:
            projected_file = file_size + stmt_size + addition + 2
        else:
            # 新規ステートメントなら、insert_prefix + addition + ";\n"
            projected_file = file_size + len(insert_prefix.encode("utf-8")) + len(values_tuple.encode("utf-8")) + 2
        if file_buf and projected_file > args.max_bytes:
            flush_stmt()
            flush_file()

        stmt_rows.append(values_tuple)
        stmt_size += addition

        rows_done += 1
        if rows_done % 50_000 == 0:
            print(f"  progress: {rows_done:,}/{total:,}", file=sys.stderr)

    flush_stmt()
    flush_file()

    con.close()
    print(f"done: {rows_done:,} rows -> chunks {args.chunk_start}..{chunk_no - 1}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
