#!/bin/sh
# Supervise the backfill: restart on transient fetch failures (exit 1),
# never on parse failures (exit 2). Cache makes each restart resume fast.
for attempt in 1 2 3 4 5; do
    .venv/bin/python -u -m scraper.run --backfill 5
    code=$?
    [ $code -eq 0 ] && echo "backfill complete" && exit 0
    [ $code -eq 2 ] && echo "parse failure — stopping, fix the parser" && exit 2
    echo "fetch failure (attempt $attempt/5), retrying in 60s..."
    sleep 60
done
echo "giving up after 5 attempts"
exit 1
