# VIX Analysis — Remaining Tasks

## P4: music-project ✅ DONE
Moved `/Users/warrenjin/agent-workspace/music-project/` → `/Users/warrenjin/music-project/`.
It was an unrelated git repo sitting in the workspace tree.

---

## P1: CBOE Strike Cutoff — Zero-Bid Truncation

**Problem:** The CBOE VIX calculation excludes strikes after two consecutive zero-bid or zero-ask prices.
This applies both to the main variance computation and to the tail (F5/F6) calculations.

**What to do:**
- Read the CBOE VIX whitepaper to get the exact methodology for strike truncation.
- Apply the two-consecutive-zero-bid/ask rule when building the chain DataFrame or computing variance.
- Ensure both the main variance sum and the F5/F6 tail calculations use the same truncated strike set.
- Regenerate the CSV after fixing.

**Reference:** Search for "CBOE VIX methodology whitepaper" online — the strike truncation rule is documented there.

---

## P2: Extraneous Functions & Cubic Spline Interpolation

**Problem:** `get_vol_at_strike` does linear interpolation. Randy specified cubic spline.

**What to do:**
- Replace all linear interpolation in the skew dicts with cubic spline interpolation.
- Remove any functions that are not used in the final pipeline (e.g., `get_vol_at_strike_from_df`, `compute_atm_iv`, `get_near_term_vol_at_strike` if truly unused).
- Audit all functions in `vix_analysis.py` — if a function is only called internally and not by `run_decomposition`, flag it.
- Use scipy's `CubicSpline` for the 30d blended vol surface interpolation.

---

## P3: Remove Near/Far After Interpolation; Fix Factor Definitions

**Core principle:** After building the 30d blended skew on a given date, the near/far expiry chains are never used again. All subsequent calculations (F1–F6) operate purely on the blended skew surface at that date.

**Current issues in `run_decomposition`:**

### `get_near_term_vol_at_strike` — REMOVE
This function reads raw near-term IV. After interpolation, we NEVER touch near-term or far-term chains again. Delete this function entirely.

### F1 and F2 — Correct them:
- **F1:** On the OLD date's blended skew, at strike = NEW spot, read the 30d vol. Subtract OLD blended ATM vol. = `σ30_old(S_new) − σ30_old(S_old_ATM)`
- **F2:** At strike = NEW spot on NEW blended skew, minus at same strike on OLD blended skew. = `σ30_new(S_new) − σ30_old(S_new)`

Both F1 and F2 should use the BLENDED 30d vol surface, NOT the raw near-term chain.

### F3 and F4 — Not 30-delta proxy:
The whitepaper uses the actual 30d vol surface to find the strikes. For each date:
- Find the strike K such that `N(d1) = 0.30` (call wing) and `N(d1)−1 = −0.30` (put wing) on the 30d blended skew.
- F3 = change in 30d put skew at the 30-delta put strike (not a "gradient" — just the raw change in vol at that strike between dates, minus F2)
- F4 = change in 30d call skew at the 30-delta call strike (same logic)
- Use `find_strike_for_delta` with blended σ30 dict (already partially done, but confirm it's using blended not raw)

### F5 and F6 — Not 10-delta:
- F5 = vol change at the 10-delta put strike minus F2 minus F3 (downside convexity)
- F6 = vol change at the 10-delta call strike minus F2 minus F4 (upside convexity)
- Same: read from blended 30d skew dict, not raw chains.

### `build_30day_skew` — Cubic spline instead of linear:
- After building the variance-interpolated vol dict, fit a `CubicSpline` to get a continuous vol surface.
- `get_vol_at_strike` should use the spline instead of linear interpolation.

### `find_strike_for_delta` — Already partially fixed:
Confirm it uses blended σ30 dict (not raw near-term). The current code may still have old references to `near_df` / `far_df`.

---

## Deliverables
1. Fix P1: Apply CBOE zero-bid truncation rule. Regenerate CSV.
2. Fix P2: Replace linear interpolation with cubic spline in skew surface. Remove unused functions.
3. Fix P3: Remove `get_near_term_vol_at_strike`. Rewrite F1 and F2 to use blended 30d vol. Confirm F3-F6 use blended skew.
4. All changes committed to `main` on `origin`.
5. Regenerate `vix_decomposition.csv` and `vix_results.txt`.
