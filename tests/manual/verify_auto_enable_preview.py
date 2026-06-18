"""Real-URL proof of the full product flow after the auto-enable fix:
detect (auto-enables) -> the preview/extract immediately shows EVERY variant,
so the per-serving calories (265) and real serving size (170 g) are visible
without the user manually ticking 'Extract every selected variant combination'.

    venv\\Scripts\\python.exe -m tests.manual.verify_auto_enable_preview
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.models.job import ExtractionMode
from app.services.fetcher import apply_interactions_and_capture, fetch_url
from app.services.interaction_detect import detect_interaction_profile
from app.services.interaction_extraction import extract_records_with_variants

URL = "https://www.calories.info/food/beef-veal"
ROW = "tr.MuiTableRow-root"


def _of(label, sel, t="string"):
    return {"name": label, "label": label, "user_label": label,
            "selector": sel, "type": t, "selected": True}


# Realistic analyzer output (the #154 inconsistent-label shape): each parallel
# column carries a qualifier, so the families collapse (via position alignment).
FIELDS = [
    _of("Food Name", "td:nth-child(1) a p"),
    _of("Serving (100g)", "td:nth-child(2)"),
    _of("Calories (per 100g)", "td:nth-child(3)", "number"),
    _of("Serving (alternative)", "td:nth-child(4)"),
    _of("Calories (alternative serving)", "td:nth-child(5)", "number"),
]


async def main() -> int:
    fetched = await fetch_url(URL, "AUTO")
    profile, new_fields = detect_interaction_profile(
        fetched.html, FIELDS, repeated_item_selector=ROW
    )
    # Replicate the endpoint's auto-enable decision.
    auto = any(g.get("execution") in ("deterministic", "mixed")
               for g in profile.get("groups") or [])
    if auto:
        profile["enabled"] = True
    print(f"groups={[(g['metadata_key'], g['execution']) for g in profile['groups']]}")
    print(f"AUTO-ENABLED={auto}  enabled={profile['enabled']}")

    # This is exactly what the preview runs (is_enabled -> variant extraction).
    spec = SimpleNamespace(mode=ExtractionMode.STRUCTURED, content_config={},
                           fields=new_fields or FIELDS, interaction_profile=profile)

    async def _cb(recipes):
        return await apply_interactions_and_capture(fetched.final_url, recipes)

    recs, warnings = await extract_records_with_variants(
        base_html=fetched.html, source_url=fetched.final_url,
        project=SimpleNamespace(analysis={"repeated_item_selector": ROW}),
        spec=spec, max_records=2000, fetch_variant_htmls=_cb,
    )
    skey = next((f.get("user_label") for f in (new_fields or FIELDS)
                 if "serving" in str(f.get("label", "")).lower()), "Serving Size")
    print(f"\nPREVIEW would show these Beef rows ({len(recs)} total records):")
    shown = 0
    for r in recs:
        d = r.normalized_data
        if str(d.get("Food Name") or d.get("Food") or "").strip() != "Beef":
            continue
        print(f"  serving_basis={str(d.get('serving_basis')):16} "
              f"unit={str(d.get('unit_system')):9} "
              f"serving={str(d.get(skey)):20} calories={d.get('Calories')}")
        shown += 1
        if shown >= 4:
            break
    got_170 = any("portion" in str(r.normalized_data.get(skey) or "").lower()
                  for r in recs)
    print(f"\nRESULT: {'PASS — preview shows real per-serving sizes' if (auto and got_170) else 'FAIL'}")
    return 0 if (auto and got_170) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
