# Flask Log Analyzer + Gemini (Quota-Friendly)

Une API Flask **bien structurée** pour analyser un fichier log (ex: 26k lignes), regrouper les erreurs par signature,
compter leurs occurrences, *et n'appeler Gemini qu'une seule fois par type d'erreur unique* (avec **cache**).
Génère aussi un **rapport PDF** téléchargeable.

## Installation

```bash
cd flask_log_analyzer
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# éditez .env et mettez votre GEMINI_API_KEY
python app.py
```

## Endpoints

- `GET /health` — ping
- `POST /processLogFile` (multipart/form-data) — champs:
  - `file`: votre fichier `.log`
  - `language` (optionnel): `fr` (défaut) ou `en`
  - `top_k` (optionnel): limiter au top K erreurs par occurrence
  - `min_count` (optionnel): ignorer les erreurs avec moins de N occurrences
- `GET /download/<report_id>` — récupère le PDF

### Exemples Postman
- **POST** `http://localhost:5000/processLogFile`
  Form-Data:
  - file: *<votre_fichier.log>*
  - language: fr
  - top_k: 10
  - min_count: 2

Réponse JSON:
```json
{
  "summary": {
    "unique_error_types": 5,
    "total_errors_counted": 124,
    "report_id": "uuid",
    "report_url": "/download/uuid"
  },
  "groups": [
    {
      "signature": "a1b2c3d4e5f6a7b8",
      "representative_message": "2025-08-12 10:02:01 ERROR ...",
      "normalized_message": "<TIMESTAMP> ERROR ...",
      "count": 87,
      "solution": "Causes probables..."
    }
  ]
}
```

## Pourquoi ça économise le quota Gemini ?
- On **normalise** les messages (on enlève timestamps, IDs, chemins, etc.) pour les **regrouper**.
- On appelle Gemini **une seule fois** pour chaque *signature unique* (hash du message normalisé).
- On **met en cache** les solutions (`cache/solutions.json`). Appels suivants => 0 coût.

## Adapter les patterns
Dans `src/services/log_parser.py`, ajustez `ERROR_PATTERNS` et `NORMALIZERS` pour votre format de logs.
