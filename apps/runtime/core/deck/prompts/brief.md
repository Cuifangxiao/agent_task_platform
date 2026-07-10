# PPTAgent Brief Node Prompt

Extract a source-backed deck brief from the request and uploaded text.

Return only valid JSON:

```json
{
  "facts": [
    {"id": "fact#1", "statement": "specific fact", "value": "optional", "unit": "optional", "source_file": "optional"}
  ],
  "audience": "who this deck is for",
  "goal": "what the deck should accomplish",
  "occasion": "optional context",
  "language": "zh|en",
  "suggested_pages": 8
}
```

Rules:
- Do not fabricate facts, values, or sources.
- Use stable `fact#N` ids.
- Keep fact statements concise and useful for slide writing.
