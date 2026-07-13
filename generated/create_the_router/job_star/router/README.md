# Job-Star Router

Picks the right AI model for a task based on **complexity**, **urgency**,
**cost budget**, and **model availability**. Uses [LiteLLM](https://github.com/BerriAI/litellm)
as the underlying model gateway so any supported provider works.

## Endpoints

| Method | Path         | Purpose                                      |
|--------|--------------|----------------------------------------------|
| GET    | `/health`    | Liveness + per-tier availability.            |
| GET    | `/tiers`     | List configured model tiers and metadata.    |
| POST   | `/route`     | Return a routing decision only.              |
| POST   | `/complete`  | Route + execute a completion via LiteLLM.    |

## Routing inputs

- `complexity` (1–10): how hard the task is.
- `urgency` (`now` | `soon` | `later`): time pressure.
- `cost_budget_cents`: optional cap on estimated cost.
- `prefer_quality`: bias toward a heavier tier when ambiguous.

## Tier catalog

Tiers are ordered cheapest → most capable: `nano`, `micro`, `standard`, `heavy`.
Each tier has a complexity band; the router picks the tier whose band contains
the task complexity, then applies an urgency/quality bias and filters by
budget and availability.

## Running
