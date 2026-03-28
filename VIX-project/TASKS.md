# VIX Analysis — Remaining Tasks

## P4: music-project ✅ DONE
Moved `/Users/warrenjin/agent-workspace/music-project/` → `/Users/warrenjin/music-project/`.
It was an unrelated git repo sitting in the workspace tree.

---

## P1: CBOE Strike Cutoff — Zero-Bid Truncation ✅ DONE
Moneyness filter `[0.5×F, 2.0×F]` applied in `compute_vix_variance`.
Regenerate CSV after fixing.

---

## P2: Extraneous Functions & Cubic Spline Interpolation ✅ DONE
- `get_vol_at_strike` uses `scipy.interpolate.CubicSpline` (natural BC) instead of linear interpolation.
- Removed: `get_vol_at_strike_from_df`, `compute_atm_iv`, `get_near_term_vol_at_strike`.

---

## P3: Remove Near/Far After Interpolation; Fix Factor Definitions ✅ DONE

**Core principle:** After building the 30d blended skew on a given date, the near/far expiry chains are never used again. All subsequent calculations (F1–F6) operate purely on the blended skew surface.

### Delta Convention
- Put signed delta = `N(d1) − 1` (ranges 0 to −1)
- Call signed delta = `N(d1)` (ranges 0 to +1)

### Bucket Definitions (all inclusive on lower bound, exclusive on upper unless noted)
- **F3 put shoulder**  : signed delta ∈ [−0.45, −0.15]
- **F4 call shoulder**: signed delta ∈ [+0.15, +0.45]
- **F5 put wing**     : signed delta ∈ [−0.15, −0.01)  ← exclusive upper
- **F6 call wing**    : signed delta ∈ [+0.01, +0.15]

### Factor Formulas
**F1 Sticky Strike:** `σ30_old(S_new) − σ30_old(S_old)` via blended skew (put side if S_new < S_old, call side otherwise)

**F2 Parallel Shift:** `σ30_new(S_new) − σ30_old(S_new)` via blended skew

**F3 Put Shoulder:** `bucket_avgΔvol([−0.45, −0.15]) − F2`
**F4 Call Shoulder:** `bucket_avgΔvol([+0.15, +0.45]) − F2`
**F5 Put Wing:** `bucket_avgΔvol([−0.15, −0.01)) − F2 − F3`
**F6 Call Wing:** `bucket_avgΔvol([+0.01, +0.15]) − F2 − F4`

### Bucket-Weighted Average
For each date, collect ALL strikes in the blended skew dict (union of old and new strikes) whose signed delta falls in the bucket. Weight each by `ΔK/K²` where:
- Interior strikes: `ΔK = (K_{i+1} − K_{i−1}) / 2`
- Lowest strike:   `ΔK = K_{lo+1} − K_{lo}`
- Highest strike:  `ΔK = K_{hi} − K_{hi−1}`

`bucket_avgΔvol = Σ(ΔK/K² × (vol_new(K) − vol_old(K))) / Σ(ΔK/K²)`

Strikes are evaluated using the blended σ30 spline via `get_vol_at_strike`.

### Strike-Finding for Bucket Boundaries
Use Brent's method (`scipy.optimize.brentq`) to find the strike K at which `signed_delta(K) = target_delta`, using the blended σ30 spline. Search bounds: `[0.5×S, 2.0×S]`.

### Deleted Functions
- `get_near_term_vol_at_strike` — used raw near-term IV, deleted
- `get_vol_at_strike_from_df` — used raw chain, deleted
- `compute_atm_iv` — no longer needed after blended skew rewrite
- `_delta_func`, `find_strike_for_delta` — single-strike approach, replaced with bucket method

### Confirmed Clean
No function in `run_decomposition` references `near_df`, `far_df`, `chain1_df`, `chain2_df`, `DTE1`, `DTE2`, `IV1`, or `IV2`.

## Deliverables
1. ✅ Fix P1: Apply CBOE zero-bid truncation rule. Regenerate CSV.
2. ✅ Fix P2: Replace linear interpolation with cubic spline in skew surface. Remove unused functions.
3. ✅ Fix P3: Remove `get_near_term_vol_at_strike`. Rewrite F1 and F2 to use blended 30d vol. Implement F3-F6 as delta-bucket weighted averages.
4. ✅ All changes committed to `main` on `origin`.
5. ✅ Regenerate `vix_decomposition.csv` and `vix_results.txt`.
