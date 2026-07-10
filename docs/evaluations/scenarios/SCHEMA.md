# Scenario Card Schema

Every JSON scenario card has exactly these fields:

| Field | Meaning |
| --- | --- |
| `case_id` | Stable, URL-safe evaluation identifier. |
| `raw_request` | The original user request, without runtime rewriting. |
| `language` | Request language or language mix. |
| `scenario_family` | Matrix family, such as ambiguous intent or compound constraints. |
| `core_outcome_direction` | Human review anchor for the desired outcome, not a query or expected repository. |
| `optional_constraints` | Explicitly optional constraints or known unknowns. |
| `risk_level` | `low`, `medium`, or `high` review risk. |
| `evaluation_date` | Card creation or review date in ISO format. |
| `reviewer` | Scenario-card reviewer or `pending-independent-review`. |
| `source` | Origin or authorship context without personal or private data. |
| `redaction` | How sensitive data was removed or why none was present. |

The runner rejects cards with unrecognized fields. This is structural schema
validation only; no card value is used to alter parsing, search planning,
evidence collection, scoring, or report wording.

