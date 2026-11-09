# Forma Atlas — product design system

Revision 7 · 2026-10-28 · UI UX Pro Max implementation pass.

## Direction

Modern corporate SaaS with a distinctive emerald identity. Light botanical navigation and workspace surfaces contrast with a rich deep-pine hero. Chartreuse is used for brand emphasis and primary hero actions. Preserve the user's constraints: no blue platform theme, no black-and-white theme, no beige/bronze direction. Recognizable integration service marks retain their original colors.

## Skill sources and application

Installed skill: /home/test/.codex/skills/ui-ux-pro-max/SKILL.md, original nextlevelbuilder/ui-ux-pro-max-skill.

Queries used: `SaaS productivity dashboard` design system, `premium SaaS dashboard` style domain, `SaaS professional` typography domain, and `text contrast readable dashboard` UX domain.

Applicable guidance: modular dashboard cards, clear hierarchy, Plus Jakarta Sans, semantic tokens, consistent icon family, focus visibility, responsive reflow and measured contrast. Generated marketing conversion patterns were not applied to this authenticated application. Do not treat skill output as runtime evidence.

## Tokens

- Canvas --canvas: #eaf1e8
- Surface --surface: #f5faef
- Secondary surface --surface-alt: #e3efde
- Deep brand --pine: #153f35
- Hover brand --pine-light: #235748
- Accent --lime: #d4f2a4
- Text --ink: #244839
- Secondary text --muted: #526e5b
- Borders --line: #cdddc9
- Focus --focus: #286948
- Font: Plus Jakarta Sans, system-ui fallback; display=swap
- Spacing: 8/16/24/32px, adaptive content gutters
- Radius: 8px controls, 12–14px objects, 18px hero

## Components and behavior

public/style.css is one consolidated stylesheet, replacing the previous series of theme overrides. Shared treatment covers agent cards, forms, integration tabs, tables/charts, billing, agent management, auth dialogs and email settings.

The library has a branded feature panel with a dedicated generated artwork asset, capability labels, factual availability counters, search, status filters, grid/list layout, empty-state reset and responsive menu. `Live` denotes agent visibility and does not assert provider, connection or worker readiness. Browser history records navigation; Back/Forward re-render the corresponding workspace.

A skip link and visible focus support keyboard navigation. Hero art is decorative and hidden from assistive technology. Service icons have text alternatives. Do not hide generation blockers or publishing consent for visual cleanliness.

## Assets and provenance

- Product logo: public/brand/atlas.png
- Brand artwork: public/brand/atlas-hero.png
- Service marks: public/brand/linkedin.svg, youtube.svg
- Interface icons: local Lucide 0.468.0; license in LUCIDE_LICENSE

Product and artwork assets were created with the built-in Image Gen tool. Prompts and output provenance are recorded in BRANDING.md. Source originals remain in the generated-images folder. Assets are referenced locally and allowed explicitly by the server.

## Verification

- JavaScript syntax checks passed for app.js and youtube.js.
- server.py byte-compilation and 2 isolated server regression tests passed.
- Browser checks: text search, Live filter, grid/list switching, browser Back, mobile menu navigation to YouTube, admin YouTube integration tab.
- No horizontal page overflow measured at 375, 768, 1024 and 1440px for the tested library/YouTube/admin states.
- Desktop visual inspection completed through native browser screenshot. Full-page screenshot API was unavailable; do not claim full-page visual comparison.
- Measured card body contrast: 5.36:1; inactive filter text: 4.81:1; hero body text on its solid backing: 7.62:1. Sidebar nav text was darkened after measuring a 4.46:1 initial value.
- Production integrations were not invoked and publishing settings were not changed. Preview keeps LinkedIn scheduler paused.
