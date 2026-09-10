# Business-flow curation — living-codebase-cartographer

Flows are **generic**: the scanner emits deterministic skeletons
(`flow_candidates`, `business-flows/_candidates.md`) seeded from any entry
kind — endpoint, route, queue/topic, event, job — and you promote only real
end-to-end flows, each as `business-flows/<flow>.md`:

```markdown
# Order creation (Python + FastAPI + React example)

> Status: evidenced | Confidence: HIGH | Verified: <commit>

User → `OrderPage` (`frontend/src/App.tsx:4`)
→ `POST /orders` (`backend/main.py:21`, handler `create_order`)
→ `Order` model → `orders` table (migration `a1b2c3`, creates)
→ `https://payments.example.com/charge` (external-service)
← response → React state → UI

Evidence:
- endpoint: `endpoint:POST /orders` (HIGH)
- handler edge: handled-by … (HIGH)
- downstream: calls/reads/consumes … (MEDIUM/LOW — state which)
- tables/collections/queues: …

Open questions / UNKNOWNs:
- … (anything not backed by evidence goes here, never in the narrative)
```

Rules:

- Every step cites file + symbol + edge confidence. Steps without evidence go
  under "Open questions", not the narrative.
- Canonical shapes (pick what the evidence supports, never force one):
  - request/response: User → Component → API client → Endpoint → Handler →
    Service/Function → Data access → Table/Collection → Response → State → UI
  - event-driven: Event → Consumer → Logic → Data → Event
  - scheduled: Job → Worker → External API → Data
- Do not assume a frontend, REST API, or relational DB exists — check
  `stack.md` and seed kinds first (`map-flow` works from any seed kind).
- Never invent flows. If only a skeleton exists, leave it in `_candidates.md`.
