-- docsia-addons : schéma de suivi des récoltes.
-- Modélise le cycle de vie complet d'un document, phase par phase :
--   1 déclenchement      -> recolte_runs (trigger)
--   2 scrapping          -> recolte_items.discovered_at
--   3 structuration/stock-> recolte_items.stored_at   (sha256 + cache local)
--   4 ingestion Paperless-> recolte_items.imported_at (paperless_id)
--   5 métadonnées        -> recolte_items.metadata_at (custom fields posés)
--   6 ingestion IA       -> recolte_items.embedded_at (chunks embeddés côté DOCSIA)
--   7 disponibilité      -> recolte_items.available_at (interrogeable dans le chat)

CREATE SCHEMA IF NOT EXISTS recolte;

-- 1. Catalogue des sources (une par addon/modalité)
CREATE TABLE IF NOT EXISTS recolte.sources (
    source_key   text PRIMARY KEY,           -- ex. 'pv-ca'
    label        text NOT NULL,
    modality     text,                        -- ex. 'html-table-scrape'
    config       jsonb NOT NULL DEFAULT '{}', -- URLs, options, etc.
    cadence      text,                        -- ex. 'weekly', 'after-session'
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- 2. Exécutions (un run par déclenchement)
CREATE TABLE IF NOT EXISTS recolte.runs (
    id            bigserial PRIMARY KEY,
    source_key    text NOT NULL REFERENCES recolte.sources(source_key),
    run_id        text NOT NULL,
    trigger       text NOT NULL DEFAULT 'manual',  -- manual | schedule | n8n
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'RUNNING', -- RUNNING | OK | PARTIAL | FAILED
    pages_scanned integer NOT NULL DEFAULT 0,
    discovered    integer NOT NULL DEFAULT 0,
    stored        integer NOT NULL DEFAULT 0,
    imported      integer NOT NULL DEFAULT 0,
    metadata_done integer NOT NULL DEFAULT 0,
    embedded      integer NOT NULL DEFAULT 0,
    errors        integer NOT NULL DEFAULT 0,
    last_error    text,
    notes         jsonb NOT NULL DEFAULT '{}',
    UNIQUE (source_key, run_id)
);

-- 3. Documents (un par ressource découverte), avec cycle de vie phase par phase
CREATE TABLE IF NOT EXISTS recolte.items (
    id                  bigserial PRIMARY KEY,
    source_key          text NOT NULL REFERENCES recolte.sources(source_key),
    run_id              text,                 -- dernier run l'ayant touché

    -- identité source (clé de dédoublonnage)
    origin_url          text NOT NULL,
    container_url       text,
    member_path_in_zip  text,

    -- métier
    seance_date         date,
    role                text,                 -- PV | PV_ANNEXE | DELIB | PJ
    delib_number        text,                 -- CA-AAAA-NNN si connu
    source_title        text,
    expected_title      text,

    -- entreposage
    local_path          text,
    mime_type           text,
    size_bytes          bigint,
    sha256              text,

    -- Paperless
    paperless_id        integer,
    paperless_checksum  text,

    -- cycle de vie (NULL = phase non atteinte)
    discovered_at       timestamptz,
    stored_at           timestamptz,
    imported_at         timestamptz,
    metadata_at         timestamptz,
    embedded_at         timestamptz,
    available_at        timestamptz,

    -- état
    status              text NOT NULL DEFAULT 'DISCOVERED',
    failed_phase        text,                 -- scrap|store|import|metadata|ia
    error               text,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS items_identity_idx
ON recolte.items (
    source_key, origin_url,
    coalesce(container_url,''), coalesce(member_path_in_zip,'')
);
CREATE INDEX IF NOT EXISTS items_sha256_idx        ON recolte.items(sha256);
CREATE INDEX IF NOT EXISTS items_paperless_id_idx  ON recolte.items(paperless_id);
CREATE INDEX IF NOT EXISTS items_expected_title_idx ON recolte.items(expected_title);

-- Vue : phase courante lisible + drapeaux de complétude, pour Metabase.
CREATE OR REPLACE VIEW recolte.items_phase AS
SELECT
    i.*,
    CASE
        WHEN i.available_at IS NOT NULL THEN '7-disponible'
        WHEN i.embedded_at  IS NOT NULL THEN '6-ia'
        WHEN i.metadata_at  IS NOT NULL THEN '5-metadonnees'
        WHEN i.imported_at  IS NOT NULL THEN '4-paperless'
        WHEN i.stored_at    IS NOT NULL THEN '3-stocke'
        WHEN i.discovered_at IS NOT NULL THEN '2-decouvert'
        ELSE '1-inconnu'
    END AS phase,
    (i.failed_phase IS NOT NULL) AS en_erreur
FROM recolte.items i;

-- Vue : complétude par source (pour le dashboard : dernière récolte, avancement, erreurs).
CREATE OR REPLACE VIEW recolte.completude AS
SELECT
    s.source_key,
    s.label,
    s.cadence,
    (SELECT max(finished_at) FROM recolte.runs r WHERE r.source_key = s.source_key) AS derniere_recolte,
    count(i.*)                                        AS total,
    count(i.*) FILTER (WHERE i.stored_at   IS NOT NULL) AS stockes,
    count(i.*) FILTER (WHERE i.imported_at IS NOT NULL) AS importes,
    count(i.*) FILTER (WHERE i.metadata_at IS NOT NULL) AS metadonnes,
    count(i.*) FILTER (WHERE i.embedded_at IS NOT NULL) AS embeddes,
    count(i.*) FILTER (WHERE i.available_at IS NOT NULL) AS disponibles,
    count(i.*) FILTER (WHERE i.failed_phase IS NOT NULL) AS en_erreur
FROM recolte.sources s
LEFT JOIN recolte.items i ON i.source_key = s.source_key
GROUP BY s.source_key, s.label, s.cadence;
