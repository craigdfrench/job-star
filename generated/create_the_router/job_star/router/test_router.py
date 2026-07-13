curl -s localhost:8100/route -H 'Content-Type: application/json' \
  -d '{"complexity": 6, "urgency": "soon", "cost_budget_cents": 2.0}'


// --- DUPLICATE BLOCK ---

{
  "tier": "standard",
  "model": "anthropic/claude-3-5-sonnet-20240620",
  "rationale": "complexity=6, urgency=soon, bias=0; selected standard",
  "estimated_cost_cents": 0.54,
  "alternatives": ["micro", "heavy"],
  "routed_at": 1730000000.0
}


// --- DUPLICATE BLOCK ---

curl -s localhost:8100/route -H 'Content-Type: application/json' \
  -d '{"complexity": 6, "urgency": "soon", "cost_budget_cents": 2.0}'


// --- DUPLICATE BLOCK ---

{
  "tier": "standard",
  "model": "anthropic/claude-3-5-sonnet-20240620",
  "rationale": "complexity=6, urgency=soon, bias=0; selected standard",
  "estimated_cost_cents": 0.54,
  "alternatives": ["micro", "heavy"],
  "routed_at": 1730000000.0
}
