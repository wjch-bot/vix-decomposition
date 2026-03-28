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

### F1 and F2 — Use blended 30d vol only:
- **F1:** On the OLD date's blended skew, at strike = NEW spot, read the 30d blended vol. Subtract OLD blended ATM vol. = `σ30_old(S_new) − σ30_old(S_old_ATM)`
- **F2:** At the same strike (NEW spot) on NEW blended skew, minus OLD blended vol at that strike. = `σ30_new(S_new) − σ30_old(S_new)`
- Both use the blended 30d skew surface. The "near-term near-ATM" language from the old code is incorrect — there is no near-term after interpolation.

Both F1 and F2 should use the BLENDED 30d vol surface, NOT the raw near-term chain.

### F3 and F4 — 15 to 45 delta bucket (inclusive), inversely strike-weighted:
Per the whitepaper, F3 and F4 use a DELTA BUCKET (not a single strike):
- F3 (put skew): all strikes in the 15–45 delta bucket (i.e., signed delta in [−0.45, −0.15])
- F4 (call skew): all strikes in the 15–45 delta bucket (i.e., signed delta in [0.15, 0.45])
- For each date, find ALL strikes within the bucket. Weight each strike's vol contribution by `1/K²` (inversely proportional to strike squared), same as the VIX formula.
- F3 = weighted_avg_vol_bucket(put_new, [−0.45, −0.15]) − weighted_avg_vol_bucket(put_old, [−0.45, −0.15]) − F2
- F4 = same for calls
- The whitepaper calls this "skew" — it is a weighted average vol in the bucket, not a single-strike delta proxy.

### F5 and F6 — 1 to −15 delta bucket (inclusive lower, exclusive upper), inversely strike-weighted:
- F5 (downside convexity): delta bucket [−0.15, −0.01] (i.e., signed delta > −0.15 and ≤ −0.01, or equivalently: 1 ≥ |delta| > 0.01)
- F6 (upside convexity): delta bucket [0.01, 0.15] (i.e., signed delta in [0.01, 0.15])
- Same inversely strike-weighted average (`1/K²`) approach.
- F5 = weighted_avg_vol_bucket(put_new, [−0.15, −0.01]) − weighted_avg_vol_bucket(put_old, [−0.15, −0.01]) − F2 − F3
- F6 = same for calls

### Delta convention:
- Put signed delta = `N(d1) − 1` (ranges 0 to −1)
- Call signed delta = `N(d1)` (ranges 0 to +1)
- 45-delta put = −0.45, 15-delta put = −0.15
- 1-delta put = −0.01, 15-delta call = +0.15

### Strike-finding for delta buckets:
- For each bucket boundary (e.g., −0.45, −0.15, −0.01, 0.01, 0.15), find the strike K that gives that signed delta using the 30d blended vol surface and Brent's method.
- Then collect all strikes in the blended skew dict that fall between those delta bounds.
- Compute the weighted average vol using `Σ(ΔK/K² × vol)` / `Σ(ΔK/K²)`.

### `build_30day_skew` — Cubic spline instead of linear:
- After building the variance-interpolated vol dict, fit a `CubicSpline` to get a continuous vol surface.
- `get_vol_at_strike` should use the spline instead of linear interpolation.

### `find_strike_for_delta` — Already partially fixed:
Confirm it uses blended σ30 dict (not raw near-term). The current code may still have old references to `near_df` / `far_df`.

---

### Prerequisite for P3: Clean up old incorrect code
Before rewriting, delete or comment out ALL old incorrect implementations so they don't cause confusion:
- Delete `get_near_term_vol_at_strike` (uses raw near-term IV, wrong)
- Delete or gut `find_strike_for_delta` and `_delta_func` (was for single-strike, not buckets)
- Delete `get_vol_at_strike_from_df` (uses raw near-term chain, wrong)
- Delete `compute_atm_iv` (no longer needed after removing near/far usage)
- Delete any commented-out old F1/F2/F3/F4/F5/F6 calculations
- Rewrite `run_decomposition` from scratch using the correct methodology above.
- Make sure NO function in the final code references `near_df`, `far_df`, `chain1_df`, `chain2_df`, `DTE1`, `DTE2`, `IV1`, `IV2` after the blended skew is built.

## Deliverables
1. Fix P1: Apply CBOE zero-bid truncation rule. Regenerate CSV.
2. Fix P2: Replace linear interpolation with cubic spline in skew surface. Remove unused functions.
3. Fix P3: Remove `get_near_term_vol_at_strike`. Rewrite F1 and F2 to use blended 30d vol. Confirm F3-F6 use blended skew.
4. All changes committed to `main` on `origin`.
5. Regenerate `vix_decomposition.csv` and `vix_results.txt`.
