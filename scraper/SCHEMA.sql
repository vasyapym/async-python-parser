CREATE TABLE IF NOT EXISTS listings (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('purchase', 'product')),
    external_id TEXT NOT NULL,
    number TEXT,
    title TEXT NOT NULL,
    description TEXT,
    customer_name TEXT,
    status TEXT,
    published_at TIMESTAMPTZ,
    deadline_at TIMESTAMPTZ,
    price NUMERIC(18, 2),
    currency TEXT,
    url TEXT NOT NULL,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS listings_source_published_idx
    ON listings (source, published_at);

CREATE TABLE IF NOT EXISTS attachments (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT,
    size BIGINT,
    sha256 TEXT,
    local_path TEXT,
    status TEXT NOT NULL,
    error TEXT,
    downloaded_at TIMESTAMPTZ,
    UNIQUE (listing_id, source_url)
);

CREATE TABLE IF NOT EXISTS scraper_checkpoints (
    source TEXT PRIMARY KEY,
    cursor_at TIMESTAMPTZ NOT NULL,
    saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
