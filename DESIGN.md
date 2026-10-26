# PaperStudio interface

PaperStudio is a Chinese-language research workspace built with native JavaScript and CSS. Preserve its quiet green identity and concentrate on paper discovery, reading, evidence and collaboration. The workspace is an Operate surface; PDF/reflow content is a Read surface. This is a refinement of the existing product, not a marketing-site redesign.

## Visual vocabulary

- One system sans family with Chinese fallbacks; no remote font or animation dependency.
- Canvas `#f6f7f4`, content white, primary text `#20372e`, metadata `#566a60`.
- Accent `#24664e` denotes actions and selection; inactive surfaces stay neutral.
- Body 16px, controls 14px, metadata 13px. Product headings use a fixed rem scale.
- Spacing uses 4/8/12/16/24/32px. Containers use 12px radii, controls 8px.
- Light separators `#d8e0d9` are decorative. Essential form boundaries use `#7a8d81`; keyboard focus uses the accent.
- Borders establish content grouping; shadows are reserved for overlays. No decorative page entrances.

`web/design-system.css` is the final semantic layer. Reader geometry, PDF text coordinates and note rectangles are owned by the existing reader and never rescaled through generic type rules.

## Interaction rules

- Show paper results early. Use one-column research lists, compact source counts and progressive advanced filters.
- Selected filters remain visible and individually removable. Retain existing results while fetching, and clearly label stale results after errors.
- Navigation has a current state and skip link. At 900px or below, the workspace navigation opens on demand with Escape, focus return and background isolation.
- Reader settings live on the right. Narrow screens show one document at a time and expose tools on demand; the reader center is reserved for the paper.
- Selecting text does not launch a model or open notes. Right-click or its keyboard equivalent exposes the four explicit actions.
- Citations remain collapsed until opened. Research lineage and structured analysis run only after explicit interaction.
- Background refresh must preserve question drafts, form input and focus. Use native disclosure and dialog semantics where possible.
- Ordinary users never receive administrator configuration controls. Visual refinement must not weaken backend permissions or provenance.

## Sources and adaptation

- [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/blob/main/.claude/skills/ui-ux-pro-max/SKILL.md): readability, keyboard outcomes, label wrapping and restrained knowledge-base patterns. The broad design-system searches matched lab marketing/CRM surfaces and were rejected; focused domain guidance was adapted to this product.
- [Impeccable](https://github.com/pbakaus/impeccable/blob/main/skill/SKILL.src.md), especially Operate/Read, polish and craft-floor guidance: preserve identity, clarify hierarchy, resolve inaccessible states and validate the whole path. The context launcher/detector was unavailable; source guidance and project code were reviewed directly.

Token contrast checks pass 4.5:1 for the authored primary/secondary/action/error/warning text pairs; this is not a blanket WCAG conformance claim. Controller tests, stylesheet parsing and code review complement but do not replace rendered visual and touch testing. Browser navigation returned HTTP 401 in this environment, so actual browser screenshots and gesture QA remain unverified.
