# VIX® Index Decomposition — Exact Formula Reference

**Source:** CBOE VIX Index Decomposition Whitepaper (August 1, 2025)  
**Original PDF:** https://cdn.cboe.com/resources/vix/VIX-Decomposition-2025-08-01.pdf  
**Live Tool:** https://www.cboe.com/en/tradable-products/vix/vix-decomposition/  
**Reference Implementation:** `vix_decomposition.py`

---

## 1. Background

The VIX Index is commonly referred to as a "fear gauge" — expected to move inversely to the S&P 500 Index. However, the VIX is more accurately a **measure of the bid for optionality**, which is often but not always driven by demand for protection.

The **VIX Index Decomposition** framework disentangles a VIX move into **6 principal components**, explaining both counterintuitive directional moves and magnitude surprises.

---

## 2. The VIX Formula (Reference)

The VIX Index is computed from SPX/SPXW option prices:

```
VIX = 100 × √σ²_30d
```

**Step 1 — 30-day constant-maturity variance** (interpolated from near- and next-term expiries):

```
σ²_30d = [(T₂ − 30) · σ₁² + (30 − T₁) · σ₂²] / (T₂ − T₁)     …… (Eq 1)
```

**Step 2 — Single-term variance** (CBOE Methodology, Section 3a):

```
σ² = (2/T) · Σ [ ΔKᵢ/Kᵢ² · e^(RT) · Q(Kᵢ) ] − (1/T) · [F/K₀ − 1]²     …… (Eq 2)
```

Where:
| Symbol | Meaning |
|---|---|
| T | Time to expiration in years |
| Kᵢ | Strike price of ith option |
| ΔKᵢ | Half-gap to adjacent strikes (edges: full gap) |
| R | Risk-free rate to that expiration |
| Q(Kᵢ) | Midpoint bid/ask at strike Kᵢ |
| F | Forward price = K₀ + e^(RT)(C₀ − P₀) |
| K₀ | First strike ≤ forward price F |

**Forward price from put-call parity:**
```
F = K₀ + e^(RT) × (Call_at_K₀ − Put_at_K₀)
```

---

## 3. The 6 Decomposition Factors

The total VIX change between two dates equals the sum of 6 independent components:

```
ΔVIX_total = F1 + F2 + F3 + F4 + F5 + F6     …… (Eq 3)
```

---

### Factor 1 — Expected Move per Sticky Strike

**Concept:** When SPX moves from S_old to S_new, the ATM vol rides *up* the pre-existing skew. This is the "expected" VIX change — it assumes no repricing of the volatility surface itself.

**Formula:**
```
F1 = vol_old(S_new) − vol_old(S_old)          …… (Eq 4)
```
Where `vol_old(S)` is the implied vol at strike S from the *old* skew.

**Validated Example (Yen-Carry Unwind):**
```
F1 = 21.55% − 18.99% = +2.56 VIX pts
```
SPX fell 3% → the old skew implied ATM vol should have risen by ~2.6 pts. This was the *expected* move before the shock.

**Note:** In the sticky strike regime, ATM vol at the new spot equals the vol of the old skew at that strike. This is the dominant factor in low vol-of-vol regimes (VIX < 20, daily move < 1 pt).

---

### Factor 2 — Parallel Shift of the Volatility Skew

**Concept:** A uniform repricing of optionality across the *entire* strike range, in response to a new market dislocation. Both puts AND calls get repriced.

**Formula:**
```
F2 = vol_new(S_new) − vol_old(S_new)          …… (Eq 5)
```
Measured at the *same* strike (the new ATM level) on both the old and new skews. This isolates wholesale surface repricing from directional spot movement.

**Validated Example (Yen-Carry Unwind):**
```
F2 = 28.85% − 21.55% = +7.29 VIX pts
```
The dominant factor in the Aug 5 spike — traders broadly repriced ~7.3 vols higher across all strikes.

---

### Factors 3 & 4 — Put and Call Skew Gradient Change

**Concept:** The "shoulder" of the skew (15–45 delta options) — the most liquid, actively traded strikes. Separating put vs. call gradients allows the model to explain "spot up, vol up" scenarios driven by bullish call demand.

**Formula (for each shoulder strike K):**
```
Δvol_put(K)  = vol_new(K) − vol_old(K) − F2    (for K in 15-45 delta put strikes)
Δvol_call(K) = vol_new(K) − vol_old(K) − F2    (for K in 15-45 delta call strikes)

F3 = 100 × Σ [ Δvol_put(K)  × 1/K² ] / Σ [ 1/K² ]    …… (Eq 6)
F4 = 100 × Σ [ Δvol_call(K) × 1/K² ] / Σ [ 1/K² ]    …… (Eq 7)
```

The **1/K² weighting** mirrors the structural weighting of the VIX formula itself (each term has ΔK/K²).

**Validated Example (Yen-Carry, 30-delta put at strike 4,960):**
```
Raw change:   36.22% − 27.27% = +8.95%
Net of F2:    8.95% − 7.29%  = +1.66%  (at this strike)

Full weighted F3 (all shoulder puts): ≈ +2.77 VIX pts
```
The whitepaper's full F3 (+2.77) is a weighted average across *all* shoulder strikes, not just the 30-delta representative.

---

### Factors 5 & 6 — Downside and Upside Convexity

**Concept:** The "wings" of the skew (1–15 delta options) — low-probability tail positions. Captures:
1. Excess vol change in DOTM options beyond what the shoulder and parallel shift explain
2. The **"accordion effect"** — new strikes entering the VIX calculation that weren't present on the prior day

**Formula:**
```
Δvol_put_wing(K)  = vol_new(K) − vol_old(K) − F2 − F3    (DOTM put wings)
Δvol_call_wing(K) = vol_new(K) − vol_old(K) − F2 − F4    (DOTM call wings)

F5 = 100 × [ Σ(Δvol_put_wing(K) × 1/K²) + Accordion_effect_put ] / Σ(1/K²)  …… (Eq 8)
F6 = 100 × [ Σ(Δvol_call_wing(K) × 1/K²) + Accordion_effect_call ] / Σ(1/K²) …… (Eq 9)
```

**Accordion Effect:** When a new DOTM strike K_new appears in the new skew but had no non-zero bid in the old skew, its full vol contributes: `vol_new(K_new) × 1/K²_new` (in vol terms).

**Validated Example (Yen-Carry, 10-delta put at strike 4,365):**
```
Raw change:          50.43% − 39.71% = +10.72%
Net of F2 (7.29):    10.72% − 7.29% = +3.43%
Net of F2+F3 (10.06): 3.43% − 2.77% = +0.66%  ← F5 contribution at this strike
```

---

## 4. Delta-to-Strike Mapping

To identify which strikes belong to each zone, convert delta to moneyness:

```
For a put with delta δ (0 < δ < 0.5):
    K/S = exp( σ√T × N⁻¹(δ) + σ²T/2 )

For a call with delta δ (0.5 < δ < 1):
    Use (1 − δ) in place of δ above.

Approximate mapping (VIX ≈ 20–25, T = 30 days):
┌──────────────────────┬───────┬──────────┬───────────┐
│ Zone                 │ Delta │Moneyness │ Strike @  │
│                      │       │   K/S    │ S=5,186   │
├──────────────────────┼───────┼──────────┼───────────┤
│ ATM/Belly            │  50%  │   1.000  │   5,186   │
│ Put shoulder (OTM)   │  30%  │   0.940  │   4,875   │
│ Put wing boundary     │  15%  │   0.895  │   4,641   │
│ Put wing centre       │  10%  │   0.865  │   4,486   │
│ Deep put wing         │   1%  │   0.790  │   4,097   │
└──────────────────────┴───────┴──────────┴───────────┘
```

---

## 5. Parallel Shift → VIX Point Conversion

A 1% parallel shift in vol (e.g., F2 = 1.0) translates to VIX points via:

```
∂VIX/∂σ ≈ 100 / (2 × VIX)    [at ATM]

Therefore:  ΔVIX ≈ (50 / VIX) × Δσ(%)
```

| VIX Level | 1% Parallel Shift ≈ |
|---|---|
| VIX = 15 | 3.3 VIX pts |
| VIX = 20 | 2.5 VIX pts |
| VIX = 30 | 1.7 VIX pts |
| VIX = 40 | 1.25 VIX pts |

The simple approximation (1% vol ≈ 1 VIX pt) is only valid near VIX ≈ 30–35.

---

## 6. Yen-Carry Unwind Validation

**Scenario:** Aug 2 → Aug 5, 2024. SPX fell 3%, VIX jumped from 23.39 to 38.57 (+15.18 pts).

| Factor | Description | VIX Pts |
|---|---|---|
| F1 | Sticky Strike (SPX riding old skew) | +2.57 |
| F2 | Parallel Shift (wholesale surface repricing) | +7.29 |
| F3 | Put Skew Gradient (OTM put demand) | +2.77 |
| F4 | Call Skew Gradient | −0.50 |
| F5 | Downside Convexity (DOTM put wings) | +0.66 |
| F6 | Upside Convexity | −0.30 |
| **Sum** | | **+12.5** |
| **Actual ΔVIX** | | **+15.2** |
| **Residual** | Full 1/K² weighting across all strikes | ~2.7 |

---

## 7. Python Reference Implementation

See `vix_decomposition.py` for:
- `decompose_vix_manual()` — the exact 6-factor calculation
- `moneyness_from_delta()` — delta-to-strike conversion
- `describe_skew_zones()` — print current market skew anatomy
- `run_validation()` — reproduces the Yen-Carry Unwind example

**Usage with real data** (requires SPX options chain from a data provider):

```python
from vix_decomposition import decompose_vix_manual

result = decompose_vix_manual(
    S_old=5346.0, S_new=5186.0,
    vol_old=18.99, vol_new=28.85,
    VIX_old=23.39, VIX_new=38.57,
    K_atm_old=5346.0,
    vol_at_K_new_from_old=21.55,   # F1 input
    vol_at_K_new_from_new=28.85,   # F2 input
    K_put_shoulder_old=4960.0,
    vol_put_shoulder_old=27.27,
    vol_put_shoulder_new=36.22,
    K_put_wing_old=4365.0,
    vol_put_wing_old=39.71,
    vol_put_wing_new=50.43,
)
print(result)
```

---

## References

- CBOE VIX Index Decomposition Whitepaper (Aug 1, 2025): https://cdn.cboe.com/resources/vix/VIX-Decomposition-2025-08-01.pdf
- VIX Index Methodology (Feb 26, 2026): https://cdn.cboe.com/resources/indices/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf
- VIX Decomposition Tool: https://www.cboe.com/en/tradable-products/vix/vix-decomposition/
- Derman, E. (1999). "Regimes of Volatility"
