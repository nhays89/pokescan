---
name: tune-classical-analyzer
description: Calibrate the OpenCV back-whitening analyzer on real listing photos using debug images, then adjust thresholds safely. Use when verdicts look wrong, after the first real photos arrive, or before raising max_analyses_per_day.
---
Rules first: glare never causes FAIL; an untrustworthy outline or any ambiguity => REVIEW, never PASS; do not trade a false reject for a false clear.
1. Set `analysis.classical.debug_dir: data/debug`, `analysis.enabled: true`, `analyzer: classical`, a small `max_analyses_per_day`; run `python -m pokescan.main --once`.
2. Open `data/debug/<item>/<n>_found.jpg` (green outline, BACK/NOT BACK + blue score) and `<n>_zones.jpg` (red = counted whitish, cyan = glare, green = checked band, numbers = per-zone whitish fraction).
3. Triage ~20 listings by eye against the verdicts in `select * from matches` / `assessments`. Common failures: card not found (outline), front mistaken for back or vice versa (`back_blue_min`), glare read as whitening, sleeve/toploader edges, bright background.
4. Adjust only the YAML knobs under `analysis.classical` / `analysis.policy` first (`whitening_thresholds`, `glare_*`, `blur_min`, `back_blue_min`, `max_severity`). Edits hot-reload.
5. If code must change (`pokescan/analysis/classical.py`), reproduce with a synthetic case in `tests/test_classical.py` first, then fix, then run the suite several times (tests use random noise).
6. Record what you learned in `docs/VERIFY.md` row 12.
