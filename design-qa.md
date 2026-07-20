# PPT 对话工作台 Design QA

**Comparison Target**

- Source visual truth: `C:\Users\cfx\AppData\Local\Temp\codex-clipboard-06be4106-4a25-45ee-b273-49f1cf85eb3a.png`
- Browser-rendered implementation: `C:\Users\cfx\.codex\visualizations\2026\07\20\019f7fa8-d4a7-7f41-a86b-0b2e8fbed632\ppt-conversation-desktop-final.png`
- Desktop viewport: `2048 × 1024`, light theme, PPTAgent route, running task selected, two generated-page previews visible.
- Mobile evidence: `C:\Users\cfx\.codex\visualizations\2026\07\20\019f7fa8-d4a7-7f41-a86b-0b2e8fbed632\ppt-conversation-mobile-final.png`, `500 × 1000`.
- State note: the source contains one running task; the implementation intentionally contains running, completed, and failed conversation examples so the requested multi-conversation structure can be verified. The selected running task and preview content remain comparable.

**Full-view Comparison Evidence**

- Combined source/implementation image: `C:\Users\cfx\.codex\visualizations\2026\07\20\019f7fa8-d4a7-7f41-a86b-0b2e8fbed632\ppt-conversation-comparison.png`.
- The application shell, teal-neutral visual language, flat bordered surfaces, compact typography, and real slide imagery remain consistent with the source.
- The core information architecture intentionally changes from stacked form/task/event panels to a three-region conversation workspace: conversation list, message stream with composer, and persistent page preview rail.
- The former below-the-fold event wall is replaced by sparse phase messages and a folded technical-log area; this directly resolves the original inability to see and scroll through the full workflow.

**Focused Region Comparison Evidence**

- Enlarged core-workspace comparison: `C:\Users\cfx\.codex\visualizations\2026\07\20\019f7fa8-d4a7-7f41-a86b-0b2e8fbed632\ppt-conversation-focus-comparison.png`.
- This focused pass was required because chat labels, action controls, progress text, and slide thumbnails were too small to judge reliably in the full-view comparison.

**Required Fidelity Surfaces**

- Fonts and typography: uses the existing application font stack and weights; title, section, message, metadata, and status hierarchy are visually distinct. No actionable wrapping, truncation, line-height, or optical-weight issue remains at 2048, 1250, 820, or 500 px widths.
- Spacing and layout rhythm: shell alignment and page margins remain grounded in the source. The new three-column grid uses consistent 8–14 px component gaps, restrained radii, and independent internal scroll areas on wide screens; tablet/mobile fall back to natural document scrolling.
- Colors and visual tokens: existing teal accent, neutral backgrounds, borders, and semantic success/error colors are reused. Contrast remains clear for primary text, statuses, progress, and controls.
- Image quality and asset fidelity: page previews use the real generated 2560 px slide raster assets; browser checks confirmed every visible preview loaded with non-zero natural width. No placeholder or CSS-drawn slide artwork is used.
- Copy and content: each PPT task is explicitly presented as an independent conversation. The existing API cannot append a message to the same task, so the composer truthfully says “复制当前需求创建新会话” and states that prior attachments/pages are not carried over.
- Icons and controls: existing Lucide icon family is used consistently; focus indicators, labels, disabled preview states, progressbar semantics, dialog label, and reduced-motion handling remain present.

**Findings**

- No actionable P0, P1, or P2 findings remain.
- Intentional deviation: the source's standalone event timeline is not preserved as a main panel. User-facing milestones now appear as chat messages, while technical events are folded to avoid duplicate representations of the same run.

**Comparison History**

1. Initial implementation comparison found a P1 risk that the conversation composer could clip its configuration and submit controls at narrow desktop/tablet widths. The footer now wraps and the configuration selector uses a flexible minimum width. Post-fix browser geometry at 1250, 820, and 500 px confirmed the footer, selector, and submit button all remain inside the conversation column with no horizontal overflow.
2. Interaction review found a P1 risk that one global draft could be submitted against the wrong conversation after switching tasks. Drafts and attachments are now stored per conversation/new-chat context. Browser interaction confirmed an existing conversation starts with an empty draft and the original new-chat draft is restored when returning.
3. Product-language review found a P2 mismatch between “创建新版本” and the actual frontend API, which only creates a new task and cannot carry existing slide assets. The control now says “复制需求并生成,” the helper discloses the asset boundary, and copied text context is compacted rather than recursively nested.
4. Accessibility review found a P2 nested `<main>` landmark. The inner conversation area is now a labelled `<section>`.
5. Revised desktop and mobile captures were opened and compared after these fixes. No further P0/P1/P2 differences were found.

**Primary Interactions Tested**

- Restored three conversations and switched between running and completed tasks.
- Verified completed-state download action and three loaded slide previews.
- Opened and closed the labelled large-preview dialog.
- Verified per-conversation draft isolation and restoration.
- Created a fourth PPT conversation from the bottom composer.
- Checked 2048, 1250, 820, and 500 px layouts for horizontal overflow and clipped composer controls.
- Confirmed natural vertical scrolling and horizontal conversation-list scrolling on mobile.
- Checked the browser console and Vite error overlay: no errors and no overlay.

**Open Questions**

- Same-task iterative editing would require a future backend append/revise endpoint. The current frontend does not pretend that capability exists.

**Implementation Checklist**

- [x] One PPT task maps to one visible conversation.
- [x] Sparse user/agent messages replace the raw event wall.
- [x] Composer remains accessible at the bottom of the current conversation.
- [x] Page previews remain visible and expandable.
- [x] Desktop, tablet, and mobile scrolling are usable without clipped controls.
- [x] Running, completed, failed, empty, and newly created states are represented by realistic preview data.
- [x] Browser interactions, console, tests, typecheck, and production build pass.

**Follow-up Polish**

- No blocking or acceptance-impacting polish remains. A future real append/revise API could turn copied-demand conversations into true same-task iterative editing.

final result: passed
