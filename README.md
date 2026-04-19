# Fraud Detection in Receipts

An AI-powered expense-report system that extracts receipt data, enforces
company policy, and scores each claim for fraud — with full explainability
for managers. Built as a B.Tech final-year project.

![Status](https://img.shields.io/badge/status-demo-orange)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Java](https://img.shields.io/badge/java-21-red)
![License](https://img.shields.io/badge/license-MIT-green)

![Landing page](screenshots/01_landing.png)

## What it does

1. **OCR** — upload a receipt image. OpenCV deskews, denoises, and binarises
   it; Gemini Flash extracts vendor, amount, date, category, line items.
2. **Policy** — a **Drools** rule engine (Java, Spring Boot) evaluates the
   claim against the company's policy. Rules authored in plain JSON OR
   uploaded as a PDF policy document and parsed by Gemini.
3. **Anomaly detection** — an ensemble of **Isolation Forest + Autoencoder**
   scores the claim for statistical anomalies (F1 = 0.75 on held-out test set).
4. **Decision layer** — combines the Drools verdict with the ML verdict into
   a single authoritative outcome: `VALID · SUSPICIOUS · REJECTED · FRAUDULENT`
   with a recommended action and full reason codes.
5. **Human-in-the-loop** — managers review flagged claims, approve/reject with
   comments. Attachment validator catches employees uploading random files
   as "pre-approval documents"; managers can still override.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Image preprocessing | OpenCV | Fast, well-tested, pure C under the hood |
| OCR | Gemini Flash 2.5 | Understands context — infers category, handwritten amounts, unusual date formats |
| Policy engine | Drools 9 (Spring Boot) | Industry-standard rule engine; rules in DRL, authorable by non-engineers |
| Anomaly detection | scikit-learn (IF + MLPRegressor as autoencoder) | IF catches outliers, AE catches subtle patterns; ensemble beats either alone |
| Backend API | FastAPI (async) | Async Drools + ML parallel calls; auto-generated OpenAPI docs at `/docs` |
| Database | SQLAlchemy 2.0 + SQLite (dev) / PostgreSQL (prod) | Swap by changing `DATABASE_URL` |
| Frontend | Vanilla HTML / CSS / JS | No framework, no build step. Claude-inspired warm palette + serif display. |
| Notifications | Twilio voice API | Re-upload calls on corrupt images or low-confidence OCR |
| PDF generation | ReportLab | Expense report PDFs, downloadable per period |

## Architecture

```
Browser  →  FastAPI (:8765)  →  [ Gemini OCR ]  →  [ Drools ML in parallel ]  →  Decision
                                                          ↕
                                                  Spring Boot (:8080)
                                                  Drools rules engine
```

Two services, two languages, two ports. They talk over HTTP.

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/<your-username>/fraud-detection-in-receipts.git
cd fraud-detection-in-receipts
pip install -r requirements.txt
```

### 2. Configure secrets

```bash
cp .env.example .env
# Edit .env — add your Gemini API key, Twilio creds, etc.
```

Get a free Gemini API key at https://aistudio.google.com/app/apikey.

### 3. Seed the database

```bash
python scripts/generate_synthetic_claims.py --n-normal 10000 --n-fraud 1000 \
    --output data/synthetic/claims.csv
python scripts/run_data_preprocessing.py --input data/synthetic/claims.csv \
    --output data/processed
python scripts/train_models.py
python scripts/seed_db.py
```

This generates synthetic expense claims, trains the ML ensemble, and
bootstraps the database with 100 employees across 2 companies and 11,000
historical claims.

### 4. Start both services

Terminal 1 — Drools policy engine:
```bash
cd policy-service
./mvnw spring-boot:run
```

Terminal 2 — FastAPI backend:
```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765 --reload
```

### 5. Open the app

http://127.0.0.1:8765

Pick a role:
- **Employee** — submit claims
- **Manager** — review the queue
- **Admin** — configure rules, view analytics, download PDF reports

## Screenshots

<table>
  <tr>
    <td width="50%"><b>Role picker (login)</b><br/><sub>Public · no passwords — fake auth for the demo</sub><br/><img src="screenshots/02_login.png" alt="Login page"/></td>
    <td width="50%"><b>Employee dashboard</b><br/><sub>Personal claim history with verdict timeline</sub><br/><img src="screenshots/03_employee_dashboard.png" alt="Employee dashboard"/></td>
  </tr>
  <tr>
    <td><b>Submit a claim</b><br/><sub>Live pipeline status + OCR fields + context checkboxes</sub><br/><img src="screenshots/04_submit_empty.png" alt="Claim submission page"/></td>
    <td><b>Manager review queue</b><br/><sub>Filterable by status, department, attachment-flagged</sub><br/><img src="screenshots/05_manager_queue.png" alt="Manager queue"/></td>
  </tr>
  <tr>
    <td><b>Admin dashboard</b><br/><sub>KPIs, spend by department, verdict mix</sub><br/><img src="screenshots/06_admin_dashboard.png" alt="Admin dashboard"/></td>
    <td><b>Admin — rule editor</b><br/><sub>Grade / category / restrictions / custom rules, PDF & JSON import</sub><br/><img src="screenshots/07_admin_rules.png" alt="Admin onboarding / rules"/></td>
  </tr>
  <tr>
    <td><b>Analytics</b><br/><sub>Trend, verdict pie, fraud rate by department, top categories</sub><br/><img src="screenshots/08_analytics.png" alt="Analytics"/></td>
    <td><b>Claim detail</b><br/><sub>Full pipeline output: OCR, features, rule hits, ML scores, audit</sub><br/><img src="screenshots/09_claim_detail.png" alt="Claim detail"/></td>
  </tr>
</table>

## Key features

### For employees
- Guided submit flow with real-time pipeline status
- Verdict with plain-English reason codes
- Attachment drawers that actually validate (not just checkbox trust)
- Personal claims history + stats

### For managers
- Filterable review queue (status, department, text search, attachment-flagged)
- Deep claim detail: original image, OCR output, engineered features, rule hits, ML scores
- Approve/reject with comment (fully audited)
- Override attachment-validation verdicts when Gemini gets it wrong

### For admins
- Live rule editor — grade limits, per-category limits, category restrictions,
  round-number thresholds, ML cutoffs
- **Custom rules** authored as JSON or extracted from a policy PDF via Gemini
- Dashboard stats with trend charts
- Analytics page (Chart.js) with 30/90/365-day windows
- Exportable PDF reports (ReportLab)

## File layout

```
fraud-detection-in-receipts/
├── backend/               FastAPI + all the Python services
│   ├── main.py            Route + endpoint definitions
│   ├── db.py, models_db.py SQLAlchemy engine + ORM models
│   ├── auth.py            Cookie-based role auth
│   ├── companies.py       Per-tenant rule store
│   ├── ocr.py             Gemini Flash receipt OCR
│   ├── explain.py         Plain-English reason generator
│   ├── custom_rules.py    JSON rule DSL + evaluator
│   ├── rules_import.py    PDF / JSON policy parser (via Gemini)
│   ├── attachment_validator.py  Per-attachment content validation
│   ├── policy_client.py   HTTP client for Drools service
│   ├── decision_layer.py  Combines Drools + ML into final verdict
│   ├── features_online.py Real-time feature engineering
│   ├── persistence.py     Claim/verdict/audit writes
│   ├── notifications.py   Twilio voice call wrapper
│   └── static/            UI: theme, pages, components
│
├── policy-service/        Spring Boot + Drools
│   └── src/main/
│       ├── java/com/expense/     ExpenseClaim, Violation, PolicyController
│       └── resources/rules/      expense_policy.drl
│
├── src/                   Reusable ML + image modules
│   ├── image_preprocessing/
│   ├── data_preprocessing/
│   └── models/            IsolationForestScorer, AutoencoderScorer, Ensemble
│
├── scripts/               Data + model lifecycle
│   ├── generate_synthetic_claims.py
│   ├── run_data_preprocessing.py
│   ├── train_models.py
│   ├── seed_db.py
│   ├── extract_sroie_fields.py
│   └── generate_test_policy_pdfs.py
│
├── data/
│   └── companies/         Per-tenant rule JSON templates (ACME, GLOBEX)
│
└── requirements.txt
```

Not committed (see `.gitignore`):
- `.env` — secrets
- `data/sroie/` — 344MB SROIE receipt dataset (download separately)
- `data/app.db` — generated by `seed_db.py`
- `models/*.joblib` — generated by `train_models.py`

## Getting the SROIE dataset

The ICDAR 2019 SROIE dataset (scanned receipts + ground-truth JSON) is not
committed because it's 344 MB. To get it:

```bash
cd data
git clone --depth 1 https://github.com/zzzDavid/ICDAR-2019-SROIE.git sroie_src
mv sroie_src/data/img sroie/img
mv sroie_src/data/box sroie/box
mv sroie_src/data/key sroie/key
rm -rf sroie_src
```

Then `python scripts/extract_sroie_fields.py` to build a CSV of extracted
fields ready for training.

## API

Once the FastAPI server is up, Swagger UI is at **http://127.0.0.1:8765/docs**.

Highlights:
- `POST /api/submit` — full pipeline (image + metadata → verdict)
- `GET  /api/claims/mine` — current employee's claims history
- `GET  /api/claims/queue` — manager review queue (filterable)
- `POST /api/claims/{id}/review` — manager approve/reject
- `POST /api/claims/{id}/attachments/{kind}/override` — manual attachment verdict
- `GET  /api/reports/pdf` — generate an expense-report PDF
- `POST /api/companies/{id}/custom-rules/import-pdf` — parse a policy PDF into custom rules
- `PUT  /api/companies/{id}` — update company rules
- `GET  /api/analytics/summary` — dashboards data

## Model performance

On a held-out test set of 2,200 synthetic claims (9.1% fraud rate):

| Model | Precision | Recall | F1 | ROC-AUC |
|---|---:|---:|---:|---:|
| Isolation Forest | 1.000 | 0.170 | 0.291 | 0.866 |
| Autoencoder | 0.448 | 0.840 | 0.584 | 0.928 |
| **Ensemble** | **0.992** | 0.600 | **0.748** | **0.927** |

Ensemble weights (`0.20 × IF + 0.80 × AE`) and thresholds tuned via grid
search on a held-out validation split — never on the test set.

## Decision logic

```
hard_reject                 → REJECTED     + AUTO_REJECT
anomalous + soft_fail       → FRAUDULENT   + MANUAL_REVIEW
policy_pass + anomalous     → SUSPICIOUS   + MANUAL_REVIEW
soft_fail  + normal         → SUSPICIOUS   + MANAGER_REVIEW
else                        → VALID        + AUTO_APPROVE
```

Rules always win over ML. A hard policy violation (e.g. a prohibited category)
forces `REJECTED` regardless of the ML score — the spec's "rule override" principle.

## Acknowledgements

- [SROIE](https://rrc.cvc.uab.es/?ch=13) — ICDAR 2019 scanned receipt dataset
- [Drools](https://www.drools.org/) — rule engine
- Google AI Studio — Gemini Flash API
- scikit-learn, FastAPI, SQLAlchemy, ReportLab, Chart.js — infrastructure

## License

Educational / non-commercial. This is a final-year B.Tech project demonstrating
an expense-fraud detection pipeline; it is not a production system.
