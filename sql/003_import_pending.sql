-- Import asynchrone : découplage soumission / confirmation.
ALTER TABLE recolte.items ADD COLUMN IF NOT EXISTS task_uuid text;
ALTER TABLE recolte.items ADD COLUMN IF NOT EXISTS page_url  text;
