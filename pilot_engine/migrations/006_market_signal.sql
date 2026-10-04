BEGIN IMMEDIATE;

CREATE TABLE market_signal_snapshot (
    id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_url TEXT NOT NULL,
    query_json TEXT NOT NULL CHECK(json_valid(query_json)),
    observed_at_utc TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    hs6 TEXT NOT NULL,
    country_code TEXT NOT NULL,
    period INTEGER NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('AVAILABLE', 'MISSING', 'FAILED')),
    trade_value_usd_text TEXT,
    net_weight_kg_text TEXT,
    description TEXT,
    raw_sha256 TEXT,
    quality_json TEXT NOT NULL CHECK(json_valid(quality_json)),
    limitation_json TEXT NOT NULL CHECK(json_valid(limitation_json)),
    failure_category TEXT,
    CHECK ((status = 'AVAILABLE' AND trade_value_usd_text IS NOT NULL
            AND failure_category IS NULL)
           OR (status != 'AVAILABLE' AND trade_value_usd_text IS NULL))
);
CREATE TRIGGER market_signal_no_update BEFORE UPDATE ON market_signal_snapshot
BEGIN SELECT RAISE(ABORT, 'market snapshots are append-only'); END;
CREATE TRIGGER market_signal_no_delete BEFORE DELETE ON market_signal_snapshot
BEGIN SELECT RAISE(ABORT, 'market snapshots are append-only'); END;

PRAGMA user_version = 6;
COMMIT;
