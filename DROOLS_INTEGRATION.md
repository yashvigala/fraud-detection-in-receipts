# Drools Integration — Complete Reference

This document is the single source of truth for how Drools is wired into the
FYP. Every file added or changed is listed here along with the reasoning.
Kept up to date as the integration progresses.

---

## 1. The big picture

Before Drools, all company policy logic lived inside Python
(`backend/explain.py`). The project spec names Drools specifically, so we
replace the Python policy layer with a proper Drools rule engine running as
a separate Spring Boot microservice.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Browser (dashboard at http://127.0.0.1:8765)                           │
└─────────────────────────────────────────────────────────────────────────┘
                               │  uploads receipt + metadata
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend      (Python, port 8765)                               │
│  ---------------                                                        │
│  • Image preprocessing (OpenCV)                                         │
│  • OCR (Gemini Flash 2.5)                                               │
│  • ML anomaly detection (Isolation Forest + Autoencoder)                │
│  • Decision layer (combines Drools + ML results)                        │
└─────────────────────────────────────────────────────────────────────────┘
          │   async HTTP POST /api/policy/evaluate
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Spring Boot policy service      (Java, port 8080)                      │
│  -------------------------                                              │
│  • Drools rule engine 9.44                                              │
│  • Loads .drl rules from src/main/resources/rules/                      │
│  • Returns a policy JSON (rule hits, violations, policy_score, status)  │
└─────────────────────────────────────────────────────────────────────────┘
```

Two processes, two languages, two ports. They talk over HTTP.

---

## 2. Why Drools (and why a separate service)?

| Question | Answer |
|---|---|
| **Why Drools, not Python rules?** | The project spec names Drools. Company expense policies are typically written by HR/Finance (non-engineers) — Drools lets them author rules in `.drl` files that look like English, with a proper rule engine underneath. |
| **Why a separate Java service, not embedded?** | Python can't host a JVM in-process cleanly (JPype/Py4J exists but is fragile). Running Drools as its own Spring Boot service is the standard enterprise pattern — the FastAPI side never has to touch Java. Rules can be updated (just edit the .drl) without restarting the Python side. |
| **Why Spring Boot?** | It's the Java web-framework default. Gives us a production-grade HTTP server, auto-configuration, JSON binding, and standard packaging in ~50 lines of code. |
| **Why port 8080 for Drools and 8765 for FastAPI?** | Just standard defaults. 8080 is Spring Boot's default, 8765 is what the FastAPI demo uses. Nothing sensitive about either. |

---

## 3. File map — Java (Drools) side

```
D:\fyp\policy-service\                         (Spring Boot + Drools project)
│
├── pom.xml                                    Maven build file; lists Spring Boot
│                                              + Drools dependencies
├── mvnw / mvnw.cmd                            Maven Wrapper scripts — let us
│                                              build without installing Maven
│                                              globally (downloads Maven on first
│                                              run)
├── .mvn/wrapper/maven-wrapper.properties      Points mvnw at Maven 3.9.14
│
├── src/main/java/com/expense/
│   ├── PolicyServiceApplication.java          Spring Boot entry point
│   │                                          (@SpringBootApplication)
│   │
│   ├── config/DroolsConfig.java               Registers a KieContainer Spring
│   │                                          bean so PolicyController can
│   │                                          @Autowired it. Loads all
│   │                                          .drl rules at startup.
│   │
│   ├── controller/PolicyController.java       REST endpoint at
│   │                                          POST /api/policy/evaluate.
│   │                                          Runs a Drools KieSession per
│   │                                          request, computes final status
│   │                                          from violations, returns JSON.
│   │
│   └── model/
│       ├── ExpenseClaim.java                  The fact Drools reasons about.
│       │                                      25+ fields including amount,
│       │                                      category, receiptAttached, etc.
│       └── Violation.java                     One rule hit (ruleId, severity,
│                                              deduction, reason).
│
└── src/main/resources/
    ├── application.properties                 Spring config (port 8080,
    │                                          log levels)
    ├── META-INF/kmodule.xml                   Drools module config — tells
    │                                          Drools where to find rules
    └── rules/expense_policy.drl               The rules file itself
                                               (25+ rules across 10 sections)
```

### Why these specific pieces exist

- **`pom.xml`** — Maven needs this to know what libraries to fetch. Same
  role as Python's `requirements.txt`.
- **Maven Wrapper (`mvnw`)** — lets your teammates build the project without
  installing Maven separately. They just run `./mvnw spring-boot:run`.
- **`DroolsConfig.java`** — Drools needs a `KieContainer` to know which
  rules to run. We build one at startup and share it across requests.
- **`kmodule.xml`** — Drools's equivalent of a "manifest" file. Tells it
  which folder to scan for `.drl` rule files.
- **`PolicyController.java`** — the HTTP layer. Takes a JSON body, passes
  it as a fact to Drools, collects violations, returns JSON.
- **`ExpenseClaim.java`** — Drools is a Java rule engine, so facts are
  Java objects. This class has every field the rules care about.
- **`expense_policy.drl`** — the actual rules. `.drl` = Drools Rule Language.

---

## 4. Tech stack used on the Java side

| Tech | Version | Purpose |
|---|---|---|
| **JDK (OpenJDK Temurin)** | 25 | Java runtime. We configured `java.version=21` in `pom.xml` for bytecode compatibility. |
| **Spring Boot** | 3.5.0 | Web framework — gives us the HTTP server, JSON serialisation (Jackson), and dependency injection. |
| **Drools** | 9.44.0.Final | Rule engine. Three Maven artifacts: `drools-core`, `drools-compiler`, `drools-mvel`, plus `drools-xml-support` for parsing `kmodule.xml`. |
| **Maven** | 3.9.14 (via wrapper) | Build tool — resolves dependencies, compiles, packages. |
| **Jackson** | (Spring Boot transitive) | Serialises Java objects ↔ JSON for the REST endpoint. |

---

## 5. The rule file in plain English

Location: `policy-service/src/main/resources/rules/expense_policy.drl`

It defines rules in 10 sections, each section covering a category of
policy:

| Section | Rule IDs | What it covers |
|---|---|---|
| 1 — Receipt & docs | R001–R002 | Receipt required, justification for amounts > ₹2000 |
| 2 — Lodging | R010–R012 | Hotel ₹6000/night cap, pre-approval, business-trip requirement |
| 3 — Air travel | R020–R022 | Business/First class, domestic ₹15k / international ₹80k caps |
| 4 — Ground transport | R030–R036 | Airport transfer, mileage, car rental, commute non-reimbursable |
| 5 — Meals & per diem | R040–R043 | Per diem ₹3000/day, meal ₹1000/each, team meal attendee list |
| 6 — Internet | R050–R051 | IT pre-approval, daily cap ₹500 |
| 7 — Non-reimbursable | R060 | Categorically blocked: entertainment, gym, traffic fines, etc. |
| 8 — Duplicates | R070 | Same vendor + amount within 3 days |
| 9 — Weekend | R080 | Weekend submission without justification |
| 10 — Final status | R090–R092 | Approve / Flag / Reject wrap-up (currently computed in Java) |

Each rule carries a **salience** (priority) and a **severity** (`HARD` = auto
reject, `SOFT` = flag for review). The rule engine fires all matching rules,
then the controller computes final status from the count of HARD vs SOFT
violations.

---

## 6. Running it — step by step

### Start the policy service (Drools, Java)

```bash
cd D:\fyp\policy-service
./mvnw spring-boot:run
```

First run: downloads Maven (~40 MB) and then all Drools + Spring dependencies
(~200 MB) into `C:\Users\USER\.m2\repository\`. Takes ~2 minutes.

Subsequent runs: ~3 seconds to start.

You'll see `Started PolicyServiceApplication in X.X seconds` when ready.
It's listening on `http://localhost:8080`.

### Test it directly with curl

```bash
curl -s -X POST http://localhost:8080/api/policy/evaluate \
  -H "Content-Type: application/json" \
  -d '{"claimId":"T1","employeeId":"E1","department":"Marketing",
       "expenseCategory":"LODGING","amount":7500.0,"currency":"INR",
       "submittedDate":"2025-03-15","vendor":"Hotel X",
       "receiptAttached":true,"businessTrip":false}'
```

Expected response: `"policy_engine_status":"REJECTED"` with rule hits listed.

---

## 7. Known gotchas I hit (so you know them too)

| Gotcha | What happened | Fix |
|---|---|---|
| `kie-spring` dep doesn't exist on Central at 9.44 | Build failed with *"Could not find artifact org.kie:kie-spring:jar:9.44.0.Final"* | Removed the `kie-spring` dependency — we never actually used it. `KieContainer` comes from `drools-core`'s transitive `kie-api`. |
| Drools can't parse kmodule.xml | Warn at startup: *"you're trying to perform an xml related operation without the necessary xml support"* | Added `drools-xml-support` to `pom.xml`. Split out from drools-core in v9. |
| DRL pattern matching fails: *"no such identifier: isPerDiem"* | Rules like `ExpenseClaim(isPerDiem == true)` couldn't find the field | JavaBean convention strips `is` from boolean getter names. Fixed by renaming identifiers in the DRL: `isPerDiem` → `perDiem`, etc. Six properties changed. |
| Final-status rules (R090–R092) always approved everything | They read `violations.size()` but the mutating rules didn't call `update($c)`, so pattern matches were stale | Computed status in the Java controller after `fireAllRules()` instead of relying on those rules. More deterministic and easier to debug. |

---

## 8. Python side — files added

```
D:\fyp\backend\
│
├── policy_client.py        HTTP client that calls the Drools service.
│                           • build_claim_payload() — maps OCR JSON +
│                             form metadata → ExpenseClaim JSON shape
│                           • call_policy_engine() — async POST to
│                             localhost:8080/api/policy/evaluate
│                           • Graceful degradation: if Drools is down,
│                             returns a synthetic PASS response so the
│                             ML-only path still works
│
├── decision_layer.py       Pure-Python verdict aggregator.
│                           • make_decision(policy, anomaly) →
│                             {final_status, action, decision_reason,
│                             policy_summary, anomaly_summary}
│                           • Rule-override principle: hard policy
│                             violation always wins, ML cannot downgrade
│                           • 5 decision rules covering every policy × ML
│                             combination (VALID / SUSPICIOUS /
│                             REJECTED / FRAUDULENT)
│
└── main.py                 (about to be updated)
                            The submit endpoint will:
                              1. decode image
                              2. run OpenCV preprocessing
                              3. call Gemini OCR
                              4. CALL DROOLS + RUN ML in parallel (gather)
                              5. pass both to make_decision()
                              6. return the final verdict to the browser
```

### Why each file exists

| File | Why |
|---|---|
| `policy_client.py` | FastAPI needs a way to talk to the Java service. `httpx.AsyncClient` is the async equivalent of `requests`. Keeping the HTTP call in its own module means the submit endpoint in `main.py` stays readable. |
| `decision_layer.py` | The project spec defines Step 5 explicitly as a "pure Python module that weighs evidence from both engines and produces a single authoritative verdict with full explainability". We lifted this directly from your `files/decision_layer.py` and only changed the shape of `top_features` handling to match our ensemble's output. |

### Key design choices

- **Async over sync**: the submit endpoint will call Drools and the ML
  ensemble **in parallel** via `asyncio.gather()`. Both take the same OCR
  JSON as input and are independent — serialising them would double the
  request latency for no reason. This matches the spec's Step 3+4 note.
- **Graceful fallback**: if the Drools service isn't running, the client
  returns a synthetic response (`PASS`, service_available=False) so the
  rest of the pipeline continues. The browser still gets a verdict,
  just based on ML alone.
- **Category normalisation**: our UI categories are nice strings like
  "Food" and "Client Meals", but the DRL rules pattern-match against
  uppercase enums like "MEAL" and "LODGING". `_normalise_category()`
  maps between them.
- **Final verdict taxonomy**: the spec asks for VALID / SUSPICIOUS /
  REJECTED / FRAUDULENT (four states). Our earlier Python-only system
  used NORMAL / SUSPICIOUS / ANOMALOUS (three states). The new decision
  layer upgrades us to the four-state schema.

---

## 9. FastAPI wiring — DONE

`backend/main.py` (the submit endpoint) now runs this flow:

```
1.  Read uploaded file bytes
2.  Decode image (OpenCV) — 400 on fail, Twilio call if phone given
3.  Preprocess image (OpenCV: deskew → denoise → binarise)
4.  OCR via Gemini Flash 2.5
5.  Low-confidence Twilio trigger
6.  Parse overrides, build claim dict
7.  Resolve company + rules
8.  FIRE IN PARALLEL (asyncio.gather):
       a) call_policy_engine() → Drools JSON
       b) _run_ml_inference()  → (engineered, X, anomaly_result)
9.  generate_reasons() — builds the plain-English reasons list for the UI
10. final_verdict() — back-compat NORMAL/SUSPICIOUS/ANOMALOUS for the bars
11. make_decision(policy, anomaly) — canonical verdict VALID/SUSPICIOUS/
    REJECTED/FRAUDULENT + recommended action
12. Return one JSON with: images, OCR, features, anomaly_result,
    policy_result, decision, reasons, company, notifications
```

The parallel call matters because the Drools request is a network hop
(~50 ms round-trip even locally). Running it concurrently with the ML
inference saves that latency for every claim.

### New form fields on the submit endpoint

Beyond the original `employee_id`, `department`, `grade`, and override
fields, `/api/submit` now also accepts these optional checkbox flags
(HTML form: unticked checkboxes simply don't post, so defaults kick in):

| Form field | Default | Which Drools rule uses it |
|---|---|---|
| `receipt_attached` | checked | R001 — receipt required |
| `pre_approval_attached` | unchecked | R011, R020, R034, R050 |
| `is_business_trip` | unchecked | R012, R041, R080 |
| `is_per_diem` | unchecked | R040, R041 |
| `is_team_meal` | unchecked | R043 |
| `attendee_list_attached` | unchecked | R043 |
| `justification_text` | empty | R002, R080 |

---

## 10. UI changes

| File | Change |
|---|---|
| `backend/static/index.html` | Added checkbox grid + justification textarea in Stage 1; new sections for Drools policy (`stage-policy`) and Final Verdict (`stage-decision`) |
| `backend/static/styles.css` | New styles for `.checkbox-grid`, `.chk`, `.policy-summary`, `.rule-hits`, `.final-verdict`, `.reason-codes` |
| `backend/static/app.js` | `submitForm()` forwards the checkboxes; new `renderPolicy()` and `renderDecision()` functions; `renderAll()` shows both |

The dashboard now renders seven stages end-to-end:

1. Stage 1 — Submit form (with checkboxes)
2. Stage 2 — Image preprocessing (before/after)
3. Stage 3 — Gemini OCR output
4. Stage 4 — Engineered features (14 columns)
5. Stage P — **Drools policy engine** (NEW) — rule hits with severity pills
6. Stage 5 — ML anomaly detection (IF + AE bars, reasons list, top residuals)
7. Stage F — **Final verdict** (NEW) — VALID/SUSPICIOUS/REJECTED/FRAUDULENT + action code + reason codes

---

## 11. How to run everything from scratch

### Prerequisites
- JDK 17+ installed (`java -version` should print a version ≥ 17). JDK 21 or 25 is fine — we configured `java.version=21` in pom.xml for bytecode compatibility.
- Python 3.11+ with the repo's venv activated.
- `.env` at repo root with `GEMINI_API_KEY` + optional `TWILIO_*` vars.

### Terminal 1 — Drools policy service

```bash
cd D:\fyp\policy-service
./mvnw spring-boot:run
```

First run installs Maven + dependencies (~200 MB under `~/.m2/`). Subsequent
runs start in ~3 seconds. Wait for `Started PolicyServiceApplication`.

### Terminal 2 — FastAPI backend

```bash
cd D:\fyp
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765 --reload
```

Wait for `Application startup complete`. Visit http://127.0.0.1:8765.

### If you only want to test Drools directly

```bash
curl -X POST http://localhost:8080/api/policy/evaluate \
  -H "Content-Type: application/json" \
  -d '{"claimId":"T","expenseCategory":"LODGING","amount":7500,
       "receiptAttached":true,"businessTrip":false,
       "department":"Marketing","employeeId":"E1"}'
```

---

## 12. End-to-end sanity matrix (what we verified)

Every row was exercised via the running FastAPI service after integration:

| Scenario | Drools outcome | ML outcome | Final |
|---|---|---|---|
| Clean Food ₹350 | APPROVED | SUSPICIOUS (borderline) | **VALID** / AUTO_APPROVE |
| Lodging ₹7500 (no biz trip) | REJECTED, 2 HARD + 2 SOFT | ANOMALOUS | **REJECTED** / AUTO_REJECT |
| Lodging ₹7500 (with biz trip + pre-approval) | REJECTED, 1 HARD (R010) | ANOMALOUS | **REJECTED** / AUTO_REJECT |
| Entertainment any amount | REJECTED (R060 categorical) | ANOMALOUS | **REJECTED** / AUTO_REJECT |
| Meal ₹2500 no justification | FLAGGED, 2 SOFT | ANOMALOUS | **FRAUDULENT** / MANUAL_REVIEW |

The "rules override ML" principle is visible in every row: the HARD-policy
rows all end REJECTED regardless of ML score.

---

## 13. Full list of every file in the two services

### Java (Drools) side — `D:\fyp\policy-service\`

```
pom.xml
mvnw / mvnw.cmd
.mvn/wrapper/maven-wrapper.properties
src/main/java/com/expense/
    PolicyServiceApplication.java    (generated by Spring Initializr)
    config/DroolsConfig.java         (hand-written)
    controller/PolicyController.java (from your files/, + status-computation fix)
    model/ExpenseClaim.java          (from your files/)
    model/Violation.java             (from your files/)
src/main/resources/
    application.properties           (hand-written)
    META-INF/kmodule.xml             (hand-written)
    rules/expense_policy.drl         (from your files/, is-prefix fix applied)
```

### Python (FastAPI) side — `D:\fyp\backend\`

```
main.py                 (major rewrite: parallel Drools + ML + decision)
policy_client.py        (NEW — async HTTP client for Drools)
decision_layer.py       (NEW — verdict aggregation, from your files/)
static/index.html       (NEW sections for policy + decision + checkboxes)
static/app.js           (renderPolicy / renderDecision added)
static/styles.css       (new CSS for policy/decision panels + checkboxes)
explain.py              (unchanged — still produces the UI reasons)
ocr.py                  (unchanged)
features_online.py      (unchanged)
notifications.py        (unchanged)
companies.py            (unchanged)
```

---

## 14. What to say in your viva if asked each question

**Q: "Why Drools and not Python?"**
The project spec explicitly names Drools. Real enterprise expense systems
need non-engineers (HR / Finance) to author policy rules — Drools's DRL
syntax is English-like and Drools handles the execution, rule ordering
(salience), and priority resolution. A Python rule library would force
rules to be code.

**Q: "Why a separate service?"**
Drools is Java. Python can't host a JVM cleanly. The standard enterprise
pattern is a Spring Boot microservice that exposes HTTP endpoints — the
Python side treats it like any other REST dependency.

**Q: "Why asyncio.gather?"**
Drools is a network call (~50 ms). ML inference takes ~30 ms. Serialising
them costs 80 ms; running them concurrently costs 50 ms. This matches the
spec's "Steps 3+4 in parallel" note.

**Q: "What if Drools is down?"**
`policy_client.py` catches every httpx exception, returns a synthetic PASS
response with `service_available=False`, and the UI shows a warning banner.
The claim still gets a verdict — based on ML alone — so the pipeline never
hard-fails on Drools availability.

**Q: "How do new policies get added?"**
Edit `src/main/resources/rules/expense_policy.drl`, add a new rule block,
restart the service. Drools compiles DRL at startup. For zero-downtime
updates, Drools supports hot-reload via a `KieFileSystem` pattern — we'd
migrate to that when a client needs it.

**Q: "How does one DRL serve multiple companies?"**
Right now it doesn't — there's one rules file. To scale to multi-tenant
Drools we'd either (a) load per-company DRL files keyed on company_id in
the controller, or (b) parameterise the rules with a `CompanyConfig` fact
holding per-client limits. Both are ~1-day changes.

---

## 15. Glossary (so the doc is self-contained)

| Term | Meaning |
|---|---|
| **DRL** | Drools Rule Language — the `.drl` syntax rules are written in |
| **KieBase** | A compiled set of Drools rules |
| **KieContainer** | Runtime holder of one or more KieBases — shared across requests |
| **KieSession** | An execution context (one per claim evaluation) |
| **Fact** | An object Drools reasons about — here, `ExpenseClaim` |
| **Salience** | A numeric priority; higher salience rules fire first |
| **Global** | A shared variable (like `results` list) visible to all rules in a session |
| **POJO** | Plain Old Java Object — a simple class with fields + getters/setters |
| **Spring Boot** | Java web framework that gives us auto-configured HTTP + DI |
| **Maven** | Java build/dependency tool; reads `pom.xml` |
| **Maven Wrapper (mvnw)** | Scripts that auto-download Maven — teammates don't need it installed globally |
| **POM** | Project Object Model — the `pom.xml` file |
| **FastAPI** | Python async web framework serving our `/api/submit` endpoint |
| **httpx** | Async HTTP client for Python (asyncio equivalent of `requests`) |
| **asyncio.gather** | Run multiple async coroutines concurrently and wait for all |
| **Pydantic** | FastAPI's request/response schema validator |

