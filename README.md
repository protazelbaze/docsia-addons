# docsia-addons

Modules de récolte alimentant Paperless pour DOCSIA. Chaque addon suit le même cycle
de vie en 7 phases, tracé en base (`recolte.*`) et visible dans Metabase.

## Cycle de vie d'un document

1. **déclenchement** — n8n crée un run (`recolte.runs`, champ `trigger`).
2. **scrapping** — `discover()` énumère la source (garde-fou : alerte si 0 ligne).
3. **structuration / stockage** — téléchargement, magic bytes, SHA-256, extraction ZIP.
4. **ingestion Paperless** — `post_document` + poll `/api/tasks/`.
5. **métadonnées** — custom fields posés à l'import.
6. **ingestion IA** — embeddings côté DOCSIA (réconcilié par `sync-ia`).
7. **disponibilité** — interrogeable dans le chat.

**Identité = SHA-256** (jamais le titre) : un re-scrape ne ré-importe pas l'existant.
Provenance (URLs longues) en PostgreSQL ; l'historique Paperless n'est jamais renommé.

## Structure

```
docsia_addons/common/   config, db, paperless (API), hashing, runs (suivi)
docsia_addons/pv_ca/    naming (charte), discover (scrape), pipeline, backfill, CLI
sql/001_schema.sql      recolte.sources / runs / items + vues completude
```

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt install -y libreoffice     # conversion Office -> PDF
cp .env.example .env && chmod 600 .env   # renseigner
```

## Utilisation

```bash
python -m docsia_addons.pv_ca initdb          # crée le schéma recolte
python -m docsia_addons.pv_ca discover        # scrape à blanc (rapport)
python -m docsia_addons.pv_ca backfill        # dry-run CF5 sur l'existant
python -m docsia_addons.pv_ca backfill --apply
python -m docsia_addons.pv_ca run --apply     # chaîne complète (import réel)
python -m docsia_addons.pv_ca sync-ia         # phase IA/disponibilité depuis DOCSIA
```

## Créer un nouvel addon

Copier `pv_ca/`, ne réécrire que `discover()` et les règles de titre. Tout le reste
(`fetch/reconcile/import/metadata/record`, suivi `recolte.*`) vient de `common/`.

## Déploiement conteneurisé (cible)

L'addon est un conteneur outil, sans service permanent. Il rejoint les réseaux Docker
de Paperless (`paperless_paperless_network`) et de docsia (`docker_default`), donc il
joint `paperless_app`, `paperless_postgres` et `docsia_postgres` par leur nom, sans
publier de port sur l'hôte.

```bash
# sur le serveur, dans le clone du dépôt
cp .env.example .env && chmod 600 .env   # renseigner tokens + mots de passe
docker compose build

# schéma de suivi
docker compose run --rm pv_ca initdb

# essais à blanc
docker compose run --rm pv_ca discover
docker compose run --rm pv_ca backfill

# exécutions réelles
docker compose run --rm pv_ca backfill --apply
docker compose run --rm pv_ca run --apply
docker compose run --rm pv_ca controls
docker compose run --rm pv_ca sync-ia
docker compose run --rm pv_ca prune-cache --apply
```

Orchestration n8n : déclencher `docker compose run --rm pv_ca <cmd>` selon la cadence
de la source. Le cache persiste dans le volume `/data/docsia-addons/cache`.

Note : l'image embarque LibreOffice (conversion Office), elle pèse ~1 Go. Build une fois,
réutilisée à chaque run.
