#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pg_slowwatch.py — tail PostgreSQL logs, detect slow queries and expose counters for Prometheus textfile collector.

Features:
- Parses 'duration: <ms> ms' from PostgreSQL logs (typичный формат).
- Threshold configurable (e.g., 500 ms).
- Emits Prometheus-compatible metrics file atomically (.tmp -> rename).
- Handles log rotation (inode change or truncation).
- Optional labels (env=prod,instance=db01, etc).
- Tracks count and total duration (for avg/ratios rules).

Usage:
  ./pg_slowwatch.py --log /var/log/postgresql/postgresql-15-main.log \
                    --metrics /var/lib/node_exporter/textfile_collector/slowqueries.prom \
                    --threshold-ms 500 --labels env=prod,instance=db01 --sleep 1.0

PostgreSQL log_line_prefix tips (to catch user@db):
  Example: log_line_prefix = '%m [%p] %u@%d %r '
  Script attempts to parse ' user@db ' early in the line (best-effort).
"""

import argparse
import os
import re
import sys
import time
import tempfile
import signal

DURATION_RE = re.compile(r'duration:\s+([0-9]+(?:\.[0-9]+)?)\s*ms', re.IGNORECASE)
USER_DB_RE  = re.compile(r'\s(?P<user>[^@\s]+)@(?P<db>[^\s]+)\s')  # best-effort for '%u@%d'

STOP = False

def sig_handler(signum, frame):
    global STOP
    STOP = True

for s in (signal.SIGINT, signal.SIGTERM):
    signal.signal(s, sig_handler)

def parse_labels(labels_csv):
    """
    Convert 'a=b,c=d' -> dict; validate name/value for Prom-style label format.
    """
    labels = {}
    if not labels_csv:
        return labels
    parts = [p.strip() for p in labels_csv.split(",") if p.strip()]
    for p in parts:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        k = k.strip()
        v = v.strip()
        # simple sanitation
        k = re.sub(r'[^a-zA-Z0-9_]', '_', k)
        v = v.replace('\\', '\\\\').replace('"', '\\"')
        labels[k] = v
    return labels

def format_labels(extra, db=None, user=None):
    lbls = dict(extra)
    if db:
        lbls['db'] = db
    if user:
        lbls['user'] = user
    # stable order for deterministic output
    items = sorted(lbls.items(), key=lambda kv: kv[0])
    inside = ",".join([f'{k}="{v}"' for k, v in items])
    return f'{{{inside}}}' if inside else ""

def write_metrics_atomic(path, content):
    dname = os.path.dirname(path) or "."
    base  = os.path.basename(path)
    with tempfile.NamedTemporaryFile("w", dir=dname, prefix=f".{base}.", delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    os.replace(tmp_name, path)

def tail_follow(log_path, sleep):
    """
    Generator yielding new lines from a file, surviving rotations/truncation.
    """
    last_ino = None
    f = None

    def open_file():
        nonlocal last_ino, f
        if f:
            try:
                f.close()
            except Exception:
                pass
        f = open(log_path, "r", encoding="utf-8", errors="replace")
        st = os.fstat(f.fileno())
        last_ino = st.st_ino
        # jump to end
        f.seek(0, os.SEEK_END)

    # initial open (retry till available)
    while not STOP:
        try:
            open_file()
            break
        except FileNotFoundError:
            time.sleep(sleep)
        except Exception as e:
            print(f"[WARN] open {log_path}: {e}", file=sys.stderr)
            time.sleep(sleep)

    while not STOP:
        line = f.readline()
        if line:
            yield line
            continue

        # EOF -> check rotation/truncate
        try:
            st_now = os.stat(log_path)
            cur_ino = st_now.st_ino
            # rotated (inode changed) or truncated (size < current position)
            if cur_ino != last_ino or st_now.st_size < f.tell():
                open_file()
        except FileNotFoundError:
            # wait until reappears
            time.sleep(sleep)
        except Exception as e:
            print(f"[WARN] stat {log_path}: {e}", file=sys.stderr)

        time.sleep(sleep)

def main():
    ap = argparse.ArgumentParser(description="PostgreSQL slow query watcher -> Prometheus textfile")
    ap.add_argument("--log", required=True, help="Path to PostgreSQL log file")
    ap.add_argument("--metrics", required=True, help="Path to Prometheus textfile .prom output")
    ap.add_argument("--threshold-ms", type=float, default=500.0, help="Slow query threshold in milliseconds")
    ap.add_argument("--labels", default="", help="extra labels as 'k=v,k2=v2'")
    ap.add_argument("--sleep", type=float, default=1.0, help="polling sleep interval (sec)")
    ap.add_argument("--flush-interval", type=float, default=5.0, help="metrics write interval (sec)")
    args = ap.parse_args()

    extra_labels = parse_labels(args.labels)

    # Counters in-memory
    # Global counters
    slow_count_total = 0
    slow_ms_sum_total = 0.0
    # Per user@db
    per_key = {}  # (user,db) -> {"count": int, "sum": float}

    last_flush = 0.0

    print(f"[INFO] Watching log: {args.log}, threshold={args.threshold_ms} ms, metrics: {args.metrics}")
    for line in tail_follow(args.log, args.sleep):
        # Optional capture user/db
        m_ud = USER_DB_RE.search(line)
        user = m_ud.group("user") if m_ud else None
        db   = m_ud.group("db") if m_ud else None

        m = DURATION_RE.search(line)
        if not m:
            # not a duration line
            pass
        else:
            dur_ms = float(m.group(1))
            if dur_ms >= args.threshold_ms:
                slow_count_total += 1
                slow_ms_sum_total += dur_ms
                key = (user or "unknown", db or "unknown")
                stat = per_key.get(key)
                if not stat:
                    stat = {"count": 0, "sum": 0.0}
                    per_key[key] = stat
                stat["count"] += 1
                stat["sum"]   += dur_ms

        now = time.time()
        if now - last_flush >= args.flush_interval:
            # Compose metrics exposition
            lines = []
            # HELP/TYPE
            lines.append("# HELP pg_slow_queries_total Count of slow queries observed by log parser.")
            lines.append("# TYPE pg_slow_queries_total counter")
            lines.append("# HELP pg_slow_queries_ms_sum Sum of durations (ms) for slow queries.")
            lines.append("# TYPE pg_slow_queries_ms_sum counter")

            # Global
            lbl = format_labels(extra_labels)
            lines.append(f"pg_slow_queries_total{lbl} {int(slow_count_total)}")
            lines.append(f"pg_slow_queries_ms_sum{lbl} {slow_ms_sum_total:.3f}")

            # By user/db
            for (u, d), stat in sorted(per_key.items()):
                lbl = format_labels(extra_labels, db=d, user=u)
                lines.append(f"pg_slow_queries_total{lbl} {int(stat['count'])}")
                lines.append(f"pg_slow_queries_ms_sum{lbl} {stat['sum']:.3f}")

            content = "\n".join(lines) + "\n"
            try:
                os.makedirs(os.path.dirname(args.metrics) or ".", exist_ok=True)
                write_metrics_atomic(args.metrics, content)
            except Exception as e:
                print(f"[ERROR] writing metrics: {e}", file=sys.stderr)

            last_flush = now

        if STOP:
            break

    # Final flush on exit
    try:
        lbl = format_labels(extra_labels)
        lines = [
            "# HELP pg_slow_queries_total Count of slow queries observed by log parser.",
            "# TYPE pg_slow_queries_total counter",
            "# HELP pg_slow_queries_ms_sum Sum of durations (ms) for slow queries.",
            "# TYPE pg_slow_queries_ms_sum counter",
            f"pg_slow_queries_total{lbl} {int(slow_count_total)}",
            f"pg_slow_queries_ms_sum{lbl} {slow_ms_sum_total:.3f}",
        ]
        for (u, d), stat in sorted(per_key.items()):
            lbl = format_labels(extra_labels, db=d, user=u)
            lines.append(f"pg_slow_queries_total{lbl} {int(stat['count'])}")
            lines.append(f"pg_slow_queries_ms_sum{lbl} {stat['sum']:.3f}")
        write_metrics_atomic(args.metrics, "\n".join(lines) + "\n")
    except Exception as e:
        print(f"[ERROR] final write: {e}", file=sys.stderr)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
