# Deck Components

Each text slot that should be editable in the exported PPTX must render with `data-text`.

| component | when to use | density limit |
|---|---|---|
| `cover` | Opening page with topic and promise | title <= 42 chars, subtitle <= 96 chars |
| `agenda` | Roadmap or section list | <= 7 items, item <= 24 chars |
| `section` | Major chapter divider | title <= 36 chars |
| `content-basic` | One claim plus supporting bullets | <= 4 bullets, bullet <= 56 chars |
| `kpi-cards` | 2-4 headline numbers | value <= 10 chars, label <= 20 chars |
| `timeline` | Sequenced dates or phases | <= 5 items |
| `steps` | Process or operating model | <= 5 items |
| `comparison` | Two-sided tradeoff or before/after | <= 4 items per side |
| `quadrant` | Four lenses or prioritization matrix | exactly or near 4 short items |
| `chart-takeaway` | Chart plus editable takeaway | takeaway <= 96 chars |
| `data-table` | Compact evidence table | <= 5 rows, <= 4 columns |
| `quote` | Human voice, principle, or thesis | quote <= 120 chars |
| `summary` | Final takeaways | <= 4 items |
| `end` | Closing call to action | title <= 36 chars |
