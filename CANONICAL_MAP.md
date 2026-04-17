# TMM Meta Landing Canonical Map

Purpose:
Keep the TMM Meta landing repo clean by separating canonical deployable assets from local runtime clutter.

## Canonical deployable files
- index.html — base / generic lane
- callback.html — callback / speed lane
- commercial.html — commercial / strata lane
- trust.html — trust / accreditation lane
- testimonial.html — testimonial / review lane
- transformation.html — project proof / before-after lane
- problem-solution.html — pain/problem lane
- thanks.html — shared thank-you page
- server.py — local preview + HubSpot submission handler

## Local-only runtime files
These are not source-of-truth assets and should stay ignored / archived:
- .tunnel-url
- leads.json
- leads.log
- preview-desktop.png
- preview-mobile.png
- __pycache__/
- local-runtime/

## Source-of-truth strategy overlays
These docs sit outside the repo and are the current planning layer for future edits:
- TMM Meta landing / confirmation pack (lane messaging)
- TMM Meta execution pack
- TMM Meta upload-ready build pack

## Working rule
- Edit canonical HTML pages in this repo.
- Do not leave runtime junk in the root.
- Archive local preview/runtime artifacts under local-runtime/ if needed.
- Build new ad rows against these canonical page variants only.
