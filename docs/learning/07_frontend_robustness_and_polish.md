# 07 — Frontend Robustness and Polish

This document details the design decisions and changes made to resolve frontend layout overlapping, select element overflows, sidebar active link styling, legacy navigation prominence, and misleading Phase 1 review copy in ScrapeGPT.

## Purpose & Context

While using the ScrapeGPT BYOK Console, layout issues were encountered where long select option texts forced `Select` components to expand beyond the bounds of their parent `<section>` card, overlapping other page components. Additionally, the sidebar navigation allowed multiple links to be styled as "active" simultaneously.

After Phase 1 was tested manually, two product-copy issues remained:

- `AWAITING_SETUP` was displayed as "Needs review" even though no review/approve workflow exists yet.
- `Legacy Scrape` was visible as a normal primary navigation item, making the old `/scrape` pipeline look equivalent to the new Analysis Jobs workflow.

This polish preserves the design integrity and ensures a responsive, premium user experience on all viewport sizes.

## Design Decisions

### 1. Form Element Width Constraint
- **Problem**: Long `<select>` option text can make the browser size the select element to fit the longest option, ignoring parent boundaries.
- **Solution**: We updated `Input` and `Select` components to use `w-full` by default. We also added `min-w-0` to the `<Field>` label container.
- **Why this works**: In CSS grid/flex, container min-width defaults to `auto`. Adding `min-w-0` allows the parent grids to shrink past the natural width of their children, and `w-full` forces the child inputs/selects to conform strictly to the parent width, using standard browser text truncation with an ellipsis where needed.

### 2. Standardized Grid Layouts
- **Problem**: Arbitrary columns like `lg:grid-cols-[400px_1fr]` can experience rendering issues if Tailwind arbitrary value compiler cache is stale or unsupported in specific runtime CSS environments.
- **Solution**: We replaced arbitrary configurations with standard Tailwind columns: `lg:grid-cols-3` combined with `lg:col-span-1` (left panel) and `lg:col-span-2` (right panel).
- **Why this works**: Standard Tailwind class names compile reliably in all environments and result in a clean, balanced layout on wide displays.

### 3. Dialog Height Constraints and Overflow
- **Problem**: Dialogs with large content (like `TaskResultPanel`) were causing the window to scroll and pushing the close buttons off-screen.
- **Solution**: We constrained the `Dialog` with `max-h-full` and `overflow-hidden`, while allowing its inner content container to use `overflow-y-auto min-h-0`.
- **Why this works**: This ensures the dialog itself respects viewport boundaries, allowing only the designated content area inside to scroll.

### 4. Sidebar Exact Matching
- **Problem**: Descendant routes of parent paths (like `/jobs/new` under `/jobs`) triggered active state colors for both navigation items.
- **Solution**: Added the `end` property to React Router `NavLink` items for `/dashboard` and `/jobs` to require exact path matches.

### 4. Honest Phase 1 State Copy
- **Problem**: The UI said "Needs review" for `AWAITING_SETUP`, but Phase 1 has no review/approve route, no setup editor, and no extraction continuation action.
- **Solution**: Shared job labels now render `AWAITING_SETUP` as "Analysis complete." New Analysis workflow options say "analysis result only."
- **Why this matters**: The product should not promise an interaction that does not exist. Phase 2 can reintroduce review language when the review/setup workflow is real.

### 5. Legacy Scrape Demotion
- **Problem**: The old `/scrape/new` page remained useful for compatibility testing, but it was visually competing with the new Phase 1 Analysis Jobs flow.
- **Solution**: Kept the route accessible, but styled it as subdued navigation with an `old` marker.
- **Why this matters**: Users can still test the legacy endpoint while the primary product path is unambiguous.

---

## Code Walkthrough

### Defaulting Input/Select to `w-full`
In `frontend/src/components/ui/Input.tsx` and `frontend/src/components/ui/Select.tsx`, the base classes enforce full container width:
```tsx
export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`w-full h-10 rounded-md border border-line bg-white px-3 text-sm text-ink outline-none transition placeholder:text-muted focus:border-teal focus:ring-2 focus:ring-teal/15 ${className}`}
      {...props}
    />
  );
}
```

### Exact Route Matching in Navigation
In `frontend/src/layout/AppShell.tsx`, the navigation map uses the `end` parameter dynamically:
```tsx
const navItems = [
  { to: "/dashboard", label: "Dashboard", icon: Activity, end: true },
  { to: "/jobs", label: "Jobs", icon: List, end: true },
  // ...
];
```

### State Labels
In `frontend/src/lib/jobPolling.ts`, `AWAITING_SETUP` maps to `"Analysis complete"`. The frontend still treats it as terminal for polling purposes.

---

## Lifecycle & Flow

1. At runtime, when navigating to `/jobs/new`, only the "New Analysis" NavLink receives the active color class from React Router.
2. The grid layout computes column widths based on standard proportions (`grid-cols-3`).
3. The layout sections size their content areas. The child form, fields, and input/select components inherit the constrained width and truncate text naturally rather than overflowing.
4. When a Phase 1 job reaches `AWAITING_SETUP`, polling stops and the UI displays "Analysis complete" rather than implying a review step.
5. The legacy scrape page remains reachable but is visually marked as old.

## Things to Be Careful About

- **Ellipsis and Text Clipping**: When screen width is very small, option text in selects will clip. This is standard behavior but ensure important option labels have their critical context early in the string.
- **Custom Inputs**: Any new custom input elements created in the future must include `w-full` and be placed inside containers with `min-w-0` to prevent similar grid overflow bugs.

## Summary

This change ensures frontend layout stability by locking down input and container widths using standard Tailwind utilities, and constraining dialog heights to fix overflow. It also keeps the Phase 1 product language honest: analysis jobs are the primary workflow, legacy scrape is secondary, and review/setup language waits until the real review feature exists.
