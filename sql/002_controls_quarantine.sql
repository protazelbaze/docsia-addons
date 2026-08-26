-- Contrôles qualité + reprise/quarantaine.
ALTER TABLE recolte.items ADD COLUMN IF NOT EXISTS attempts       integer NOT NULL DEFAULT 0;
ALTER TABLE recolte.items ADD COLUMN IF NOT EXISTS quarantined_at timestamptz;
ALTER TABLE recolte.items ADD COLUMN IF NOT EXISTS content_ok     boolean;

-- Items à relancer : en échec, non mis en quarantaine.
CREATE OR REPLACE VIEW recolte.a_relancer AS
SELECT * FROM recolte.items
WHERE failed_phase IS NOT NULL AND quarantined_at IS NULL;

-- Contrôles qualité consolidés (doublons, OCR vide, quarantaine).
CREATE OR REPLACE VIEW recolte.controles AS
SELECT
  (SELECT count(*) FROM recolte.items WHERE quarantined_at IS NOT NULL)               AS en_quarantaine,
  (SELECT count(*) FROM recolte.items WHERE content_ok IS FALSE)                      AS ocr_vide,
  (SELECT count(*) FROM (SELECT sha256 FROM recolte.items WHERE sha256 IS NOT NULL
      GROUP BY sha256 HAVING count(*)>1) x)                                           AS doublons_sha256,
  (SELECT count(*) FROM (SELECT expected_title FROM recolte.items
      WHERE coalesce(expected_title,'')<>'' GROUP BY expected_title HAVING count(*)>1) x) AS doublons_titre;
