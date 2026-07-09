# PPTAgent Style Node Prompt

Choose or adjust presentation ThemeTokens.

For style selection, return either:

```json
{"themes": [ThemeTokens, ThemeTokens]}
```

or:

```json
{"selected": ["Preset Name A", "Preset Name B"]}
```

For feedback adjustment, return:

```json
{"tokens": ThemeTokens}
```

Rules:
- Keep text/background contrast accessible.
- Do not introduce CSS outside tokens.
- Prefer presets that match the audience, goal, language, and subject matter.
