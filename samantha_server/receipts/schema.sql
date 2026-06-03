-- samantha_server/receipts/schema.sql
-- Append-only SQLite schema for signed receipts.
-- WAL mode + STRICT typing enforced at creation time via store.init_store().

CREATE TABLE IF NOT EXISTS receipts (
    receipt_id        TEXT PRIMARY KEY NOT NULL,
    event_input_hash  TEXT NOT NULL,
    applied_rule_id   TEXT,                  -- NULL on dispatch_empty,
                                              -- query (Step 9),
                                              -- needs_clarification (Step 10),
                                              -- and refusal (Step 11)
    order_id          TEXT,                  -- GH-367: synthetic LIS order identifier;
                                              -- NULL for multi-order query events and
                                              -- LLM paths (whose query decision_traces
                                              -- carry parsed_order_ids in payload_json).
    next_state        TEXT NOT NULL,
    outcome           TEXT NOT NULL,
    signer_key_id     TEXT NOT NULL,
    signature         BLOB NOT NULL,         -- 64 bytes
    payload_json      TEXT NOT NULL,         -- canonical-JSON of EngineDecision
    signed_at_utc     TEXT NOT NULL          -- ISO-8601 UTC (always +00:00 suffix);
                                              -- lexicographic sort equals chronological
                                              -- order — fetch_in_window relies on this.
) STRICT;

CREATE INDEX IF NOT EXISTS idx_receipts_event_hash  ON receipts(event_input_hash);
CREATE INDEX IF NOT EXISTS idx_receipts_rule        ON receipts(applied_rule_id);
CREATE INDEX IF NOT EXISTS idx_receipts_signed_at   ON receipts(signed_at_utc);
-- M-12: outcome index for fetch_by_outcome() — avoids full table scan.
CREATE INDEX IF NOT EXISTS idx_receipts_outcome     ON receipts(outcome);
-- GH-367: order_id index for fetch_by_order_id() — avoids full table scan.
CREATE INDEX IF NOT EXISTS idx_receipts_order_id    ON receipts(order_id);

-- Append-only convention; enforced at runtime by opening the audit-side
-- connection with `PRAGMA query_only = 1` (any UPDATE/DELETE issued
-- against that connection is rejected by SQLite). Writes go through
-- the single chokepoint helper in `store.py`. SQL triggers are not
-- used (they cost more than they're worth here).
