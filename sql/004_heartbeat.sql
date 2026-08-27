-- Vivacité : battement de processus (runs) + santé de flux par phase.

-- 1. Battement sur les runs : permet de distinguer "en cours" d'un run planté.
ALTER TABLE recolte.runs ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;

-- État dérivé d'un run : EN_COURS tant que le battement est frais, SUSPECT_ARRET sinon.
DROP VIEW IF EXISTS recolte.runs_etat CASCADE;
CREATE VIEW recolte.runs_etat AS
SELECT
    r.*,
    now() - COALESCE(r.heartbeat_at, r.started_at) AS depuis_dernier_signe,
    CASE
        WHEN r.finished_at IS NOT NULL THEN r.status
        WHEN COALESCE(r.heartbeat_at, r.started_at) > now() - interval '15 minutes' THEN 'EN_COURS'
        ELSE 'SUSPECT_ARRET'
    END AS etat
FROM recolte.runs r;

-- 2. Santé de flux : pour chaque phase aval (workers externes compris), combien
--    d'items attendent et depuis quand le plus ancien est bloqué.
DROP VIEW IF EXISTS recolte.phases_sante CASCADE;
CREATE VIEW recolte.phases_sante AS
WITH b AS (
    SELECT '4-ocr-paperless'::text AS phase,
           count(*) AS en_attente, min(updated_at) AS plus_ancien
    FROM recolte.items WHERE status = 'IMPORT_PENDING'
    UNION ALL
    SELECT '6-embeddings-docsia',
           count(*), min(metadata_at)
    FROM recolte.items WHERE metadata_at IS NOT NULL AND embedded_at IS NULL
    UNION ALL
    SELECT '7-disponibilite',
           count(*), min(embedded_at)
    FROM recolte.items WHERE embedded_at IS NOT NULL AND available_at IS NULL
)
SELECT phase, en_attente, plus_ancien,
       CASE WHEN plus_ancien IS NULL THEN NULL ELSE now() - plus_ancien END AS attente_max
FROM b;
