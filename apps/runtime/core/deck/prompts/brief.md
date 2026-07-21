# PPTAgent Brief Node Prompt

Infer the presentation context from the request and uploaded text. Source facts
are extracted separately by deterministic code and are not authored by this
model.

Return only valid JSON:

```json
{
  "audience": "who this deck is for",
  "goal": "what the deck should accomplish",
  "occasion": "optional context",
  "language": "zh|en",
  "suggested_pages": 8
}
```

Rules:
- Do not output, rewrite, summarize, or invent source facts.
- Infer only audience, goal, occasion, language, and an appropriate page count.
- Prefer explicit user requirements over inference.
