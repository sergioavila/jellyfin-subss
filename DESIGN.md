# New Era Bold Hero

Bold cinematic hero with full-screen background video and dark blue fallback. Ideal for creative agencies, portfolios, landing pages editoriales, marcas de moda. AI-ready template.

Density 5/10. Variance 4/10. Motion 8/10 cinematic.

Style: Bold, Uppercase, Cinematic, Video Background. Era: 2024-2026 Editorial Cinematic. Full dark only.

Other apps should follow the full spec (video hero, massive uppercase, SVG CTA) when they are landings. This file does not remove that system. The Subss section at the end maps it onto the NAS panel.

## Colors

- Azul oscuro `#21346e` — dark surface, primary background
- Blanco `#FFFFFF` — light surface, card backgrounds (not used as page fill here)
- Negro oscuro `#161a20` — dark surface, primary background
- Gris oscuro `#1A1A1A` — dark surface, text on accent
- Azul medio `#2A4080` — secondary accent, lines
- Gris claro `#CCCCCC` — secondary text, borders, muted
- Blanco suave `#F5F5F5` — secondary surface, body text on dark
- Negro `#000000` — deep contrast surface (player only)

## Typography

- Display / Hero: Rubik 700, tight tracking
- Body: Rubik 400, 16px / 1.6, max 72ch
- UI labels / captions: Rubik 0.875rem, weight 500, slight letter-spacing
- Mono: JetBrains Mono for code, metadata, technical values
- Offline fallback: `ui-sans-serif, system-ui, sans-serif` / `ui-monospace, monospace`

Scale: Hero `clamp(2.5rem, 5vw, 4rem)` · H1 `2.25rem` · H2 `1.5rem` · Body `1rem` / `1.6` · Small `0.875rem`

## Layout

- CSS Grid. Max-width 1280px, 1.5rem side padding
- Base unit 0.5rem (8px). Section gaps `clamp(4rem, 8vw, 8rem)`
- Hero: split-screen. Features: zig-zag, never 3 equal columns
- Collapse below 768px. No `h-screen`: `min-h-[100dvh]`
- z-index: base 0 / sticky-nav 100 / overlay 200 / modal 300 / toast 500

## Elevation, motion, shapes

Video backdrop (autoplay, loop, muted) with `#21346e` fallback. Massive uppercase, line-height 0.98, negative tracking. CTA SVG 184×65. Hover scale 1.05, active 0.95.

Physics: spring stiffness 120, damping 20. Entry fade + translateY 16px over 540ms ease-out, stagger 120ms. Hover scale(1.03) + shadow lift 200ms. Page fade+slide 300ms. Only transform and opacity.

Corner radius 8px.

## Components (system)

- Primary button: 0.5rem radius, accent fill, hover 8% darken + lift, active -1px, weight 600
- Ghost: 1.5px muted border, primary text. Hover: subtle fill
- Cards: 0.5rem, surface, `0 2px 12px rgba(0,0,0,0.06)`, 1px stroke
- Inputs: label above, 1px stroke, focus 2px accent offset 2px
- Nav: surface, active = accent indicator, weight 500
- Skeletons: shimmer, no circular spinners
- Empty: icon + text + action. This app has no icon set; empty is one table row

## Do / don't

No emojis in UI. No `#FFFFFF` page or card fills. No 3-column feature grids. No `h-screen`. No "Elevate / Seamless / Unleash / Next-Gen". No Lucide/Heroicons in this app.

Do: full-screen video on landings, massive uppercase, Rubik bold, custom SVG CTA. This panel does not ship those landing devices.

---

# Jellyfin Subss — application

Utility panel for a NAS. Not a marketing site. This section maps the system above onto the implemented panel.

## Applied tokens

```css
--bg: #161a20;
--surface: #21346e;
--text: #F5F5F5;
--muted: #CCCCCC;
--line: #2A4080;
--accent: #CCCCCC;
--primary: #21346e;
--neutral: #1A1A1A;
--soft: #F5F5F5;
--radius: 8px;
--ok: #5aa36a;
--warn: #c9a227;
--danger: #c96a6a;
```

`--danger` is the nearest AA pass to spec `#c45c5c` (4.18:1 on `--bg`). Status tokens are not in the landing system; they are required by download / sync / error state.

## Contrast

YAML `button-primary` (`#21346e` fill + `#1A1A1A` text) fails WCAG on this dark UI. This app uses the component rule instead: accent fill (`#CCCCCC`) + `#1A1A1A` text. Ghost buttons: 1.5px `#CCCCCC` border, `#F5F5F5` text. Input stroke uses `--accent` (interactive 3:1); `--line` stays on decorative card/table edges. No `#FFFFFF` page or card fills; body text is `#F5F5F5`.

## Type

- UI: Rubik 400/500/600/700, body 1rem/1.6, measure 72ch for lede and about
- Labels: Rubik 0.875rem / 500
- Paths, table cells, flash, status: JetBrains Mono 0.875rem
- No hero uppercase, no negative tracking on body copy

## Layout

- `min-height: 100dvh`. Containment 1280px, side padding 1.5rem, stack gap 16px
- Brand, lede, settings copy: 72ch. Tables: full 1280px
- Below 768px: action buttons stack, full width, min-height 44px. Table stays in `overflow-x: auto`
- Paths wrap with `overflow-wrap: anywhere`. `touch-action: manipulation`. `overscroll-behavior: contain`

## Motion (this app)

Cinematic video, 540ms entry, and page transitions stay in the system for other apps. This panel only uses transform/opacity: hover `scale(1.03)` 200ms, active `scale(0.95)` + 1px press. `prefers-reduced-motion: reduce` disables them.

## Components here

- Brand: `app-logo.png` wordmark, height `clamp(64px, 18vw, 96px)`, width auto. No separate title. Favicon/store: `icon.png`
- Primary = Escanear ahora / Guardar / Se queda
- Ghost = Probar Jellyfin, Probar OpenSubtitles, Reintentar
- Nav Inicio/Cola/Revisar/Ajustes: weight 500 + accent underline when on
- Library tabs (Películas, Series, …) reuse the same tab underline as Ajustes
- Table: native layout, status colors OK / warn / danger / exacto
- Empty: one table row, no extra icon set
- Focus: 2px accent outline, offset 2px
- Toast: z-index 500, fixed bottom, surface + 8px radius. Success border `--ok`, fail `--danger`. Auto-hide 5s, Cerrar. Transform/opacity only
- Player: `#000000` stage, cues `--soft`. Real playback, not a video backdrop
- No cards-in-cards, no decorative icons, no footer brand, no video backdrop
