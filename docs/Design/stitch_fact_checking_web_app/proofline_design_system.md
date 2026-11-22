---
name: "Proofline Editorial Fact-Check"
description: "An authoritative, paper-crisp editorial design system for marketing and newsroom fact-checking workflows."
---

## Overview

Proofline is designed for marketing and editorial teams verifying claims in high-stakes PDFs—whitepapers, campaign briefs, annual reports, and press releases. The interface evokes a modern newsroom desk: calm, unhurried, and scannable, with high-contrast ink typography on warm paper surfaces. It uses a **Corporate / Editorial Minimalism** style: generous whitespace, clear typographic hierarchy, and unmistakable semantic status labels so editors can triage Verified, Inaccurate, False, and Check incomplete claims at a glance on both desktop and mobile viewports.

## Colors

The palette pairs an authoritative deep ink navy (`primary: #022448`) with warm off-white paper surfaces (`surface: #FAF9FC` and `surface-container-lowest: #FFFFFF`). Semantic verdict colors are always paired with explicit text labels and icons—never relying on color alone.

- **Verified:** Deep forest green (`#146C43` on `#E6F4EA` container) for confirmed claims.
- **Inaccurate:** Warm ochre amber (`#8A5300` on `#FFF3CD` container) for partially true or outdated claims needing correction.
- **False:** Editorial crimson (`error: #BA1A1A` on `error-container: #FFDAD6`) for refuted claims.
- **Check incomplete:** Neutral slate (`#43474E` on `#E9E7EB` container) for unverifiable claims or connection/processing interruptions.

```yaml
colors:
  verified: "#146C43"
  on-verified: "#FFFFFF"
  verified-container: "#E6F4EA"
  on-verified-container: "#0D4A2E"
  inaccurate: "#8A5300"
  on-inaccurate: "#FFFFFF"
  inaccurate-container: "#FFF3CD"
  on-inaccurate-container: "#5C3700"
  incomplete: "#43474E"
  on-incomplete: "#FFFFFF"
  incomplete-container: "#E9E7EB"
  on-incomplete-container: "#2D3037"
  light:
    primary: "#022448"
    on-primary: "#FFFFFF"
    primary-container: "#1E3A5F"
    on-primary-container: "#FFFFFF"
    secondary: "#555F70"
    on-secondary: "#FFFFFF"
    secondary-container: "#D6E0F4"
    on-secondary-container: "#121C2A"
    tertiary: "#341F00"
    on-tertiary: "#FFFFFF"
    tertiary-container: "#503300"
    on-tertiary-container: "#FFEEDC"
    error: "#BA1A1A"
    on-error: "#FFFFFF"
    error-container: "#FFDAD6"
    on-error-container: "#93000A"
    background: "#FAF9FC"
    on-background: "#1A1C1E"
    surface: "#FAF9FC"
    surface-dim: "#DAD9DD"
    surface-bright: "#FAF9FC"
    surface-container-lowest: "#FFFFFF"
    surface-container-low: "#F4F3F7"
    surface-container: "#EEEDF1"
    surface-container-high: "#E9E7EB"
    surface-container-highest: "#E3E2E6"
    on-surface: "#1A1C1E"
    surface-variant: "#E0E2EB"
    on-surface-variant: "#43474E"
    outline: "#74777F"
    outline-variant: "#C4C6CF"
    inverse-surface: "#2F3033"
    inverse-on-surface: "#F1F0F4"
    inverse-primary: "#ADC8F5"
    shadow: "#000000"
    scrim: "#000000"
  dark:
    primary: "#ADC8F5"
    on-primary: "#133155"
    primary-container: "#1E3A5F"
    on-primary-container: "#D5E3FF"
    secondary: "#BDC7DA"
    on-secondary: "#273140"
    secondary-container: "#40495A"
    on-secondary-container: "#D9E3F7"
    tertiary: "#EDBF7F"
    on-tertiary: "#452B00"
    tertiary-container: "#503300"
    on-tertiary-container: "#FFDDB2"
    error: "#FFB4AB"
    on-error: "#690005"
    error-container: "#93000A"
    on-error-container: "#FFDAD6"
    background: "#121316"
    on-background: "#E3E2E6"
    surface: "#121316"
    surface-dim: "#121316"
    surface-bright: "#38393C"
    surface-container-lowest: "#0D0E11"
    surface-container-low: "#1A1C1E"
    surface-container: "#1E2022"
    surface-container-high: "#292A2D"
    surface-container-highest: "#343538"
    on-surface: "#E3E2E6"
    surface-variant: "#43474E"
    on-surface-variant: "#C4C6CF"
    outline: "#8E9199"
    outline-variant: "#43474E"
    inverse-surface: "#E3E2E6"
    inverse-on-surface: "#2F3033"
    inverse-primary: "#455F87"
    shadow: "#000000"
    scrim: "#000000"
```

## Typography

Proofline uses **Crimson Pro** for editorial headlines and quoted claim headings, **PT Sans** for high-legibility body copy, explanations, and UI controls, and **Spline Sans Mono** for page numbers, file sizes, timestamps, and reporting periods. All `-mobile` counterparts scale down cleanly to `<= 32px` for narrow viewports.

```yaml
typography:
  display-lg:
    fontFamily: "Crimson Pro"
    fontSize: 40px
    fontWeight: 600
    lineHeight: 46px
    letterSpacing: -0.5px
  display-lg-mobile:
    fontFamily: "Crimson Pro"
    fontSize: 30px
    fontWeight: 600
    lineHeight: 36px
    letterSpacing: -0.25px
  headline-lg:
    fontFamily: "Crimson Pro"
    fontSize: 32px
    fontWeight: 600
    lineHeight: 38px
    letterSpacing: -0.25px
  headline-lg-mobile:
    fontFamily: "Crimson Pro"
    fontSize: 26px
    fontWeight: 600
    lineHeight: 32px
    letterSpacing: -0.2px
  headline-md:
    fontFamily: "Crimson Pro"
    fontSize: 24px
    fontWeight: 600
    lineHeight: 30px
    letterSpacing: 0px
  title-lg:
    fontFamily: "PT Sans"
    fontSize: 20px
    fontWeight: 700
    lineHeight: 28px
    letterSpacing: 0px
  title-md:
    fontFamily: "PT Sans"
    fontSize: 16px
    fontWeight: 700
    lineHeight: 24px
    letterSpacing: 0px
  body-lg:
    fontFamily: "PT Sans"
    fontSize: 16px
    fontWeight: 400
    lineHeight: 24px
    letterSpacing: 0px
  body-md:
    fontFamily: "PT Sans"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 21px
    letterSpacing: 0px
  label-lg:
    fontFamily: "PT Sans"
    fontSize: 14px
    fontWeight: 700
    lineHeight: 20px
    letterSpacing: 0.1px
  label-sm:
    fontFamily: "PT Sans"
    fontSize: 12px
    fontWeight: 700
    lineHeight: 16px
    letterSpacing: 0.25px
  mono-sm:
    fontFamily: "Spline Sans Mono"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 18px
    letterSpacing: 0px
```

## Layout & Spacing

Desktop uses a **Fixed-Max-Width Grid** (`1120px` max container) with generous page margins (`32px`) and clean single-column or 2-column (8/4 split) editorial layouts. Mobile (`390px`) collapses all multi-column grids to a single column with `16px` page margins and full-width (`44px` minimum height) touch targets. All spacing follows a strict `4px` / `8px` grid.

```yaml
spacing:
  gutter-xs: 4px
  gutter-sm: 8px
  gutter-md: 16px
  gutter-lg: 24px
  gutter-xl: 32px
  section: 48px
  page-margin: 24px
```

## Elevation & Depth

Proofline uses **Tonal layers** paired with **Low-contrast outlines** (`1px solid outline-variant`). Elevated cards sit on `surface-container-lowest` (`#FFFFFF`) over `surface` (`#FAF9FC`), avoiding heavy drop shadows or glassmorphism so dense editorial text stays crisp and distraction-free.

## Shapes

Corners are crisp and understated—`8px` (`DEFAULT`) for cards and dropzones, `6px` (`sm`) for buttons and inputs, and `9999px` (`full`) for verdict badges and status pills.

```yaml
rounded:
  sm: 6px
  DEFAULT: 8px
  md: 12px
  lg: 16px
  full: 9999px
```

## Motion & Micro-interaction

Motion is crisp and functional. Interactive controls respond in `120ms` (`state-change`), while expandable claim details and status banners transition in `200ms` (`surface-enter`). No decorative loops or layout-shifting animations.

```yaml
motion:
  state-change:
    duration: 120ms
    delay: 0ms
    timingFunction: ease-out
  surface-enter:
    duration: 200ms
    delay: 0ms
    timingFunction: cubic-bezier(0.2, 0, 0, 1)
```

## Voice & Content Strategy

- **Tone of voice:** Precise, objective, and helpful. Never alarmist, never chatty.
- **Capitalisation:** Sentence case for all headings, buttons, and labels. Exact verdict labels: `Verified`, `Inaccurate`, `False`, `Check incomplete`.
- **Action labels:** Verb-first and outcome-specific (`Start fact-check`, `Choose PDF file`, `Retry connection`, `Download CSV`).
- **Numbers & limits:** Always state exact units (`10 MiB`, `30 pages`, `24 hours`, `p. 4`, `Q3 2025`).

## Components

Component implementations live as individual HTML sticker sheet files in
`components/`. Each file contains the canonical, styled HTML components with
`<!-- COMPONENT: name -->` comment markers. Read each file before use.

- **Badge** → `@components/badge.html`: `badge-numeric`, `badge-dot`
- **Banner** → `@components/banner.html`: `banner-info`, `banner-with-action`, `banner-dismissible`
- **Button** → `@components/button.html`: `button-primary`, `button-icon`, `button-with-icon`
- **Card** → `@components/card.html`: `card-elevated`, `card-action`, `card-interactive`, `card-interactive-2`
