# VIX® Index Decomposition Methodology

**Source:** CBOE Volatility Index Decomposition Whitepaper (August 1, 2025)  
**Original PDF:** https://cdn.cboe.com/resources/vix/VIX-Decomposition-2025-08-01.pdf  
**Live Tool:** https://www.cboe.com/en/tradable-products/vix/vix-decomposition/

---

## 1. Background & Purpose

The VIX Index is commonly referred to as a "fear gauge" — expected to move inversely to the S&P 500 Index. However, the VIX Index is more accurately a **measure of the bid for optionality**, which is often (but not always) driven by demand for protection. Sometimes the VIX goes up not from fear of downside but from fear of missing out (increased upside SPX call demand).

CBOE's **VIX Index Decomposition** framework disentangles a given VIX Index move into **6 principal components**, allowing market observers to understand *why* the VIX moved, not just *that* it moved.

---

## 2. The 6 Decomposition Factors

The framework models the VIX Index as if calculated from a **single 30-day fixed-strike SPX implied volatility skew** (interpolated in variance space from the front and back month contracts). It then attributes the total VIX change to six factors:

### Factor 1 — Expected Move per Sticky Strike
**What it means:** Quantifies the change in VIX already priced into volatility markets given a SPX spot move, assuming implied volatility evolves according to a fixed strike skew (the "sticky strike" regime, per Derman 1999).

**Intuition:** When SPX drops, ATM implied volatility rides *up* the existing skew — this is the expected VIX move. It dominates in low-to-moderate vol-of-vol regimes.

**Example (Yen-Carry Unwind, Aug 2→5, 2024):**
```
SPX Close Aug 2  = 5,346   →  ATM implied vol = 18.99
SPX Close Aug 5  = 5,186   →  Implied vol for 5,186 strike (using Aug 2 skew) = 21.55

Expected VIX move due to sticky strike = 21.55 − 18.99 = +2.57 pts
```

**Key finding:** In "typical" regimes (VIX < 20, daily move < 1 pt), sticky strike explains ~88–100% of the VIX move. Its contribution decreases as the shock intensity increases relative to the prior VIX level.

---

### Factor 2 — Parallel Shift of the Volatility Skew
**What it means:** A wholesale repricing of optionality across the *entire* strike range, up or down.

**Intuition:**
- **Parallel upshift** → traders face a new ~2+ standard deviation dislocation; they reprice *both* puts AND calls higher across the surface. This is the dominant effect in large VIX spikes.
- **Parallel downshift** → traders gain understanding of the shock; the initial premium gradually unwinds.

**Example (Yen-Carry Unwind, Aug 2→5, 2024):**
```
ATM strike Aug 5 (SPX = 5,185):
  Implied vol from Aug 5 skew at 5,185  = 28.85
  Implied vol from Aug 2 skew at 5,185  = 21.55

  Parallel upshift = 28.85 − 21.55 = +7.29 pts
```

**Key finding:** Parallel shifts are rarely material for VIX declines when VIX < 20. They become dominant (>50% of expected move) when VIX moves 3–5+ pts from low starting levels.

---

### Factor 3 — Change in the Slope of the Put Skew Gradient
**What it means:** Marginal change in VIX from demand for the most liquid protective put options — those along the "shoulder" of the skew (15–45 delta range), represented by the ~30-delta put.

**Intuition:** Portfolio managers buy slightly OTM puts to hedge. When they rush to buy protection, the put skew steepens beyond what the parallel shift alone would cause.

**Calculation approach (abbreviated):**
```
Given: 30-delta put strike on Aug 5 = 4,960

  Implied vol of 4,960 put Aug 5  = 36.22
  Implied vol of 4,960 put Aug 2  = 27.27
  Total change                        = 8.95

  Less: parallel shift contribution  = 7.29  (from Factor 2)

  Excess steepening of put skew       = 8.95 − 7.29 = +1.66 pts
```

---

### Factor 4 — Change in the Slope of the Call Skew Gradient
**What it means:** Marginal change in VIX from demand for upside call options along the shoulder of the skew.

**Intuition:** Traders buy OTM calls to express bullish views or to fund put purchases via risk-reversals. A steepening call skew reflects demand for upside optionality — sometimes this drives VIX *up* even as SPX rises ("spot up, vol up").

**Segmentation rationale:** By separating put and call skew contributions, the model can explain VIX increases driven by bullish call demand, which would otherwise look like "broken" VIX behavior.

---

### Factor 5 — Demand for Downside Convexity
**What it means:** Marginal change in VIX from demand for deep OTM put options (1–15 delta range), i.e., tail-risk hedges.

**Intuition:** Low-probability, low-cost positions with outsized tail payoff. Demand for these creates curvature in the "wings" of the skew beyond the shoulder and parallel shift effects. Also captures the "accordion effect" — when lower strike puts activate and were not included in the prior day's VIX calculation.

**Calculation approach (abbreviated, using 10-delta put):**
```
  10-delta put strike Aug 5 = 4,365
  Implied vol Aug 5         = 50.43
  Implied vol Aug 2         = 39.71
  Total change               = 10.72

  Less: parallel shift       =  7.29
  Less: put skew gradient    =  2.77
  Excess downside convexity  = 10.72 − 7.29 − 2.77 = +0.66 pts
```

---

### Factor 6 — Demand for Upside Convexity
**What it means:** Marginal change in VIX from demand for deep OTM call options (1–15 delta range) — levered upside exposure or performance chasing.

**Intuition:** Sometimes VIX rises due to traders positioning for a rally (buying DOTM calls). This explains VIX spikes during otherwise bullish catalysts (e.g., post-Liberation Day Apr 4, 2025, where the subsequent SPX rally +7% in 2 days was anticipated by upside convexity buyers).

---

## 3. Skew Anatomy: Belly, Shoulders, and Wings

| Zone | Delta Range | Decomposition Factor |
|---|---|---|
| **Belly** (NTM/ATM) | 40–50 delta | Sticky strike + Parallel shift |
| **Shoulders** (OTM) | 15–45 delta | Put / Call skew gradient |
| **Wings** (DOTM) | 1–15 delta | Upside / Downside convexity |

---

## 4. Interpolating the 30-Day Skew (Appendix A of Whitepaper)

The decomposition uses a single synthetic 30-day skew interpolated from the front and back month SPX option contracts:

**Inputs:**
- T₁ = days to expiry, near-term contract
- T₂ = days to expiry, next-term contract
- T_target = 30 days

**Time weights:**
```
w1 = (T₂ − T_target) / (T₂ − T₁)
w2 = (T_target − T₁) / (T₂ − T₁)
```

**Variance interpolation (for each strike K):**
```
Var₁(K) = σ₁(K)² × T₁        (near-term variance)
Var₂(K) = σ₂(K)² × T₂        (next-term variance)

Var_30day(K) = w₁ × σ₁(K)² × T₁ + w₂ × σ₂(K)² × T₂
            = w₁ × Var₁(K) + w₂ × Var₂(K)

σ_30day(K) = √(Var_30day(K) / 30)
```

**Inverted weighting note:** The near-term contract (with more weight when closer to target expiry) gets weight `w1` based on distance from the *other* expiry. If T₁=29, T₂=36, T_target=30:
```
w1 = (36 − 30)/(36 − 29) = 6/7 ≈ 0.857
w2 = (30 − 29)/(36 − 29) = 1/7 ≈ 0.143
```

---

## 5. The VIX Index Formula (Reference)

The full VIX Index is calculated as the weighted average implied volatility across the active strike spectrum of two SPX option expiries:

```
VIX = 100 × √{ Σᵢ [ (2/K²) × ΔKᵢ × e^(RT) × O(Kᵢ) ] / [T × Q(Kᵢ)] }
```

Where:
- Kᵢ = strike price of ith option
- ΔKᵢ = interval between strike prices
- R = risk-free interest rate
- T = time to expiration
- O(Kᵢ) = option mid-price at strike Kᵢ
- Q(Kᵢ) = forward strike adjustment

*(See full methodology: https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf)*

---

## 6. Case Study Comparison

### Yen-Carry Unwind (Aug 5, 2024) vs. Liberation Day (Apr 4, 2025)

Both saw SPX drop 3–6%, VIX double from ~23 to ~44. Yet the decompositions reveal opposite sentiment:

| Component | Yen-Carry Unwind | Liberation Day |
|---|---|---|
| Sticky strike (expected) | Small | **Large** |
| Parallel shift | Large | Large |
| Put skew gradient | Moderate | Moderate |
| Call skew gradient | Small negative | **Moderate positive** |
| Downside convexity | **Large** | Small |
| Upside convexity | Small negative | **Very large** |
| **Sentiment** | **Bearish tail hedge** | **Bullish levered upside** |

→ Liberation Day VIX spike was partly driven by traders *positioned for a rally*, which materialized as SPX +7% over the next 2 days.

---

## 7. Using the CBOE VIX Decomp Tool

**URL:** https://www.cboe.com/en/tradable-products/vix/vix-decomposition/

Enter a "To Date" and press "Compute" — the tool automatically uses the prior trading date as "From Date" and returns the decomposition of the VIX change into the 6 components above.

---

## 8. Summary Formula Reference

The total VIX change between two dates can be expressed as:

```
ΔVIX_total =
  ΔVIX_sticky_strike        [Factor 1]
+ ΔVIX_parallel_shift       [Factor 2]
+ ΔVIX_put_skew_gradient    [Factor 3]
+ ΔVIX_call_skew_gradient   [Factor 4]
+ ΔVIX_downside_convexity   [Factor 5]
+ ΔVIX_upside_convexity     [Factor 6]
```

Each factor is computed by **recalculating the VIX Index** under a scenario where only that component changes, then taking the difference from the baseline VIX calculation.

---

## References

- CBOE VIX Index Decomposition Whitepaper (Aug 1, 2025): https://cdn.cboe.com/resources/vix/VIX-Decomposition-2025-08-01.pdf
- CBOE VIX Index Methodology: https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf
- VIX Decomposition Tool: https://www.cboe.com/en/tradable-products/vix/vix-decomposition/
- Derman, E. (1999). "Regimes of Volatility"
