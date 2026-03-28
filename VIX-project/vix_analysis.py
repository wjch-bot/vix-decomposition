#!/usr/bin/env python3
"""
VIX Analysis: Compute VIX from Supabase SPX options data (2026+)
and decompose it into 6 factors using the CBOE methodology.

Data source: Supabase market_snapshots (period=PM as close-of-day proxy)
IV: Black-Scholes IV from raw bid/ask mid-prices
VIX: CBOE two-term constant-maturity formula (Section 3b)
Decomposition: 6-factor model (imported from vix_decomposition.py)
"""

from __future__ import annotations
import os
import json
import math
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, date
from scipy.stats import norm
from scipy.optimize import brentq

from vix_decomposition import decompose_vix_manual, VIXDecomposition

# =============================================================================
# FACTOR GENERATION & HELPER FUNCTIONS
# -------------------------------------------------------------------
# Factor generation (6-factor VIX decomposition):
#   run_decomposition()           → F1-F6 computation
#   get_near_term_vol_at_strike() → F1 & F2: raw near-term IV at a strike
#   find_strike_for_delta()        → F3-F6: delta-to-strike conversion
#   _delta_func()                  → objective for find_strike_for_delta()
#
# Skew & interpolation helpers:
#   build_30day_skew()            → 30d interpolated put/call skew surface
#   get_vol_at_strike()            → linear vol interpolation from skew dict
#   get_vol_at_strike_from_df()   → vol at strike from raw chain DataFrame
#
# IV & VIX helpers:
#   bs_iv()                        → Black-Scholes implied vol (brentq)
#   _bs_call(), _bs_put()          → Black-Scholes price formulas
#   compute_vix_variance()         → CBOE variance for one expiry
#   compute_atm_iv()              → ATM IV from chain DataFrame
#   compute_forward()              → forward price via put-call parity
#   build_chain_df()              → raw optionchain → DataFrame with mids
#   find_nearest_expiries()       → near/far expiry selection (DTE <= 30 / > 30)
# =============================================================================

# CONFIG
def load_env():
    # .env is in the parent of the VIX-project directory
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    vars_ = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                vars_[k.strip()] = v.strip()
    return vars_

ENV = load_env()
SUPABASE_URL = ENV["SUPABASE_URL"]
SUPABASE_KEY = ENV["SUPABASE_SERVICE_KEY"]

# BLACK-SCHOLES IV (copied from /tmp/tastytrade-bot/methods.py)
def _bs_call(F: float, K: float, T: float, sigma: float, rfr: float) -> float:
    if sigma <= 0 or T <= 0:
        return max(F - K, 0.0) * math.exp(-rfr * T)
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return math.exp(-rfr * T) * (F * norm.cdf(d1) - K * norm.cdf(d2))

def _bs_put(F: float, K: float, T: float, sigma: float, rfr: float) -> float:
    if sigma <= 0 or T <= 0:
        return max(K - F, 0.0) * math.exp(-rfr * T)
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return math.exp(-rfr * T) * (K * norm.cdf(-d2) - F * norm.cdf(-d1))

def bs_iv(price: float, F: float, K: float, T: float, rfr: float,
          is_call: bool = True) -> float:
    """Return implied volatility in % given option price, forward, strike, T, rfr."""
    if price < 1e-8 or T <= 0:
        return 0.0
    def objective(sigma):
        if is_call:
            return _bs_call(F, K, T, sigma, rfr) - price
        else:
            return _bs_put(F, K, T, sigma, rfr) - price
    try:
        iv = brentq(objective, 1e-6, 5.0, maxiter=500)
    except ValueError:
        iv = 0.0
    return iv * 100.0

# DATA FETCHING
def fetch_snapshots_2026():
    """Fetch PM snapshots for dates >= 2026-01-01."""
    url = f"{SUPABASE_URL}/rest/v1/market_snapshots"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    params = {
        "select": "date,period,payload",
        "date": "gte.2026-01-01",
        "period": "eq.PM",
        "order": "date.asc",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()

# VIX COMPUTATION
def build_chain_df(optionchain_list: list) -> pd.DataFrame:
    """Convert raw optionchain list rows into a clean DataFrame with mid prices."""
    rows = []
    for row in optionchain_list:
        strike = float(row.get("strike", 0))
        cbid = float(row.get("cbid", 0) or 0)
        cask = float(row.get("cask", 0) or 0)
        pbid = float(row.get("pbid", 0) or 0)
        pask = float(row.get("pask", 0) or 0)
        rows.append({
            "strike": strike,
            "cmid": (cbid + cask) / 2 if cbid > 0 or cask > 0 else float("nan"),
            "pmid": (pbid + pask) / 2 if pbid > 0 or pask > 0 else float("nan"),
            "cbid": cbid, "cask": cask, "pbid": pbid, "pask": pask,
        })
    df = pd.DataFrame(rows)
    df = df.sort_values("strike").reset_index(drop=True)
    return df

def compute_forward(df: pd.DataFrame, K_atm: float, rfr: float, T: float) -> float:
    """Compute forward price via put-call parity at ATM strike."""
    atm_row = df[df["strike"] == K_atm]
    if atm_row.empty:
        idx = (df["strike"] - K_atm).abs().idxmin()
        atm_row = df.loc[[idx]]
    cmid = float(atm_row["cmid"].iloc[0])
    pmid = float(atm_row["pmid"].iloc[0])
    if math.isnan(cmid): cmid = 0.0
    if math.isnan(pmid): pmid = 0.0
    F = K_atm + math.exp(rfr * T) * (cmid - pmid)
    return F

def compute_vix_variance(df: pd.DataFrame, F: float, rfr: float, T: float) -> float:
    """
    Compute variance σ² for a single expiry using the full CBOE formula.

    σ² = (2/T) × Σ[ΔKᵢ/Kᵢ² × Q(Kᵢ)] − (1/T) × [F/K₀ − 1]²

    Where Q(K) is the actual option mid price (not discounted).
    """
    strikes = df["strike"].values
    cmids = df["cmid"].values
    pmids = df["pmid"].values

    # Find K0: first strike <= F
    K0_candidates = strikes[strikes <= F]
    if len(K0_candidates) == 0:
        return 0.0
    K0 = K0_candidates[-1]

    # Build Q(K): put mid for K<K0, call mid for K>K0, avg for K=K0
    Q = np.empty_like(strikes, dtype=float)
    for i, K in enumerate(strikes):
        if K < K0:
            Q[i] = pmids[i] if not math.isnan(pmids[i]) else 0.0
        elif K > K0:
            Q[i] = cmids[i] if not math.isnan(cmids[i]) else 0.0
        else:
            c = cmids[i] if not math.isnan(cmids[i]) else 0.0
            p = pmids[i] if not math.isnan(pmids[i]) else 0.0
            Q[i] = (c + p) / 2.0

    # Compute ΔK (strike interval using half-gap approach)
    n = len(strikes)
    dK = np.zeros(n, dtype=float)
    dK[0] = strikes[1] - strikes[0]           # lowest strike: full gap
    dK[-1] = strikes[-1] - strikes[-2]        # highest strike: full gap
    dK[1:-1] = (strikes[2:] - strikes[:-2]) / 2.0  # interior: half gap each side

    # Compute the summation term (Q is actual price, no e^(RT) needed)
    sum_term = 0.0
    for i in range(n):
        K = strikes[i]
        if K > 0 and Q[i] > 0:
            sum_term += (2.0 / T) * (dK[i] / (K ** 2)) * Q[i]

    # Forward adjustment term
    forward_adj = (1.0 / T) * ((F / K0 - 1) ** 2)

    # Variance (ensure non-negative)
    var = sum_term - forward_adj
    return max(var, 0.0)

def compute_atm_iv(df: pd.DataFrame, F: float, K_atm: float,
                  rfr: float, T: float) -> float:
    """Compute ATM IV (%) from chain DataFrame using BS IV on mid prices."""
    atm_row = df[df["strike"] == K_atm]
    if atm_row.empty:
        idx = (df["strike"] - K_atm).abs().idxmin()
        atm_row = df.loc[[idx]]
    cmid = float(atm_row["cmid"].iloc[0])
    pmid = float(atm_row["pmid"].iloc[0])
    if math.isnan(cmid): cmid = 0.0
    if math.isnan(pmid): pmid = 0.0
    ivs = []
    if cmid > 1e-6:
        iv_c = bs_iv(cmid, F, K_atm, T, rfr, is_call=True)
        if iv_c > 0: ivs.append(iv_c)
    if pmid > 1e-6:
        iv_p = bs_iv(pmid, F, K_atm, T, rfr, is_call=False)
        if iv_p > 0: ivs.append(iv_p)
    return np.mean(ivs) if ivs else 0.0

def find_nearest_expiries(optionchain: dict, snapshot_date: date,
                          target_dte: int = 30):
    """
    Find two expiries that STRADDLE target_dte days from snapshot.
    Returns (near_exp, near_dte), (next_exp, next_dte) where near_dte <= target_dte < next_dte.
    Falls back to two closest if no perfect straddle exists.
    """
    results = []
    for exp_str in optionchain.keys():
        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
        dte = (exp_dt - snapshot_date).days
        if dte > 0:
            results.append((exp_str, dte))
    results.sort(key=lambda x: x[1])

    if len(results) < 2:
        return results[:2] if results else []

    # Find where target_dte falls in the sorted DTE list
    # We want near <= target < next (bracket the target)
    near = None
    far = None
    for i, (exp_str, dte) in enumerate(results):
        if dte <= target_dte:
            near = (exp_str, dte)
        elif dte > target_dte and near is not None:
            far = (exp_str, dte)
            break
        elif dte > target_dte and near is None:
            # target is before all expiries - use two shortest
            near = (exp_str, dte)
            far = results[i + 1] if i + 1 < len(results) else results[-1]
            break

    if near is None:
        # All expiries are > target_dte
        near = results[0]
        far = results[1]
    elif far is None:
        # No expiry found > target_dte - use two longest
        near = results[-2]
        far = results[-1]

    return [near, far]

def compute_vix_for_snapshot(spot: float, rfr: float,
                               optionchain: dict,
                               snapshot_date: date) -> dict | None:
    """
    Compute 30-day constant-maturity VIX for a single snapshot.

    Returns dict with keys:
        date, spot, vix_computed, near_exp, next_exp, DTE1, DTE2,
        IV1, IV2, IV_30d, sigma_30d, rfr, K_atm1, K_atm2,
        chain1_df, chain2_df, F, T1, T2, vix_actual
    """
    # ── Find two closest expiries to 30 days ───────────────────────────────
    expiry_pair = find_nearest_expiries(optionchain, snapshot_date, target_dte=30)
    if len(expiry_pair) < 2:
        return None

    exp1_str, dte1 = expiry_pair[0]
    exp2_str, dte2 = expiry_pair[1]
    if dte1 <= 0 or dte2 <= 0 or dte1 == dte2:
        return None

    # ── Build DataFrames for each expiry ────────────────────────────────────
    df1 = build_chain_df(optionchain[exp1_str])
    df2 = build_chain_df(optionchain[exp2_str])

    T1 = dte1 / 365.0
    T2 = dte2 / 365.0

    # ── ATM strikes ─────────────────────────────────────────────────────────
    K_atm1 = float(df1.loc[(df1["strike"] - spot).abs().idxmin(), "strike"])
    K_atm2 = float(df2.loc[(df2["strike"] - spot).abs().idxmin(), "strike"])

    # ── Forward prices ──────────────────────────────────────────────────────
    F1 = compute_forward(df1, K_atm1, rfr, T1)
    F2 = compute_forward(df2, K_atm2, rfr, T2)

    # ── ATM IVs (still needed for decomposition) ───────────────────────────
    IV1 = compute_atm_iv(df1, F1, K_atm1, rfr, T1)
    IV2 = compute_atm_iv(df2, F2, K_atm2, rfr, T2)

    if IV1 <= 0 or IV2 <= 0:
        return None

    # ── Compute FULL CBOE variance for each expiry ─────────────────────────
    var1 = compute_vix_variance(df1, F1, rfr, T1)
    var2 = compute_vix_variance(df2, F2, rfr, T2)

    if var1 <= 0 or var2 <= 0:
        return None

    # ── Two-expiry constant-maturity formula ─────────────────────────────────
    # σ²_30d = [(T₂ − T₃₀)σ₁² + (T₃₀ − T₁)σ₂²] / (T₂ − T₁)
    # VIX = 100 × √(σ²_30d)  -- variance is already annual from CBOE formula
    T30 = 30.0 / 365.0

    var_30d = ((T2 - T30) * var1 + (T30 - T1) * var2) / (T2 - T1)
    if var_30d < 0:
        return None

    vix_computed = 100.0 * math.sqrt(var_30d)

    # ── 30d ATM IV (for decomposition compatibility) ───────────────────────
    IV_30d = IV1 + (IV2 - IV1) * (30 - dte1) / (dte2 - dte1)

    return {
        "date": snapshot_date.isoformat(),
        "spot": spot,
        "vix_computed": vix_computed,
        "near_exp": exp1_str,
        "next_exp": exp2_str,
        "DTE1": dte1,
        "DTE2": dte2,
        "IV1": IV1,
        "IV2": IV2,
        "IV_30d": IV_30d,
        "sigma_30d": math.sqrt(var_30d),
        "rfr": rfr,
        "K_atm1": K_atm1,
        "K_atm2": K_atm2,
        "chain1_df": df1,
        "chain2_df": df2,
        "F": F1,
        "F2": F2,
        "T1": T1,
        "T2": T2,
        "vix_actual": None,   # filled in later
    }

# SKEW & INTERPOLATION
def build_30day_skew(df_near: pd.DataFrame, df_far: pd.DataFrame,
                     dte_near: int, dte_far: int,
                     spot: float, F_near: float, F_far: float,
                     rfr: float) -> tuple[dict[float, float], dict[float, float]]:
    """
    Build 30-day interpolated put and call skews from near/far expiry chains.

    For each strike K in the union of near+far strikes:
    1. Compute IV from option price at that strike for near-expiry
    2. Compute IV from option price at that strike for far-expiry
    3. Interpolate variance to 30-day: Var30(K) = w1*Var_near(K) + w2*Var_far(K)
    4. Convert to vol: σ30(K) = √(Var30(K) × 365/30) × 100

    Returns (put_skew_30d, call_skew_30d) where each is strike→vol dict.
    """
    T_near = dte_near / 365.0
    T_far = dte_far / 365.0
    T30 = 30.0 / 365.0

    # Interpolation weights (same for all strikes)
    w1 = (dte_far - 30.0) / (dte_far - dte_near)
    w2 = (30.0 - dte_near) / (dte_far - dte_near)

    # ATM strike for classification (use near-expiry ATM)
    K_atm_near = float(df_near.loc[(df_near["strike"] - spot).abs().idxmin(), "strike"])

    # Union of all strikes
    all_strikes = sorted(set(df_near["strike"].tolist()) | set(df_far["strike"].tolist()))

    put_skew_30d = {}
    call_skew_30d = {}

    for K in all_strikes:
        if K <= 0:
            continue

        # ── Near-expiry: find nearest strike and compute IV ─────────────────
        near_strikes = df_near["strike"].values
        idx_near = np.argmin(np.abs(near_strikes - K))
        K_near_nearest = near_strikes[idx_near]
        near_row = df_near[df_near["strike"] == K_near_nearest].iloc[0]

        if K < K_atm_near:
            # Use put IV
            price_near = near_row["pmid"] if not math.isnan(near_row["pmid"]) else near_row["cmid"]
            is_put_near = True
        else:
            # Use call IV
            price_near = near_row["cmid"] if not math.isnan(near_row["cmid"]) else near_row["pmid"]
            is_put_near = False

        if math.isnan(price_near) or price_near <= 0:
            continue
        iv_near = bs_iv(price_near, F_near, K_near_nearest, T_near, rfr,
                        is_call=not is_put_near)
        if iv_near <= 0:
            continue

        # ── Far-expiry: find nearest strike and compute IV ──────────────────
        far_strikes = df_far["strike"].values
        idx_far = np.argmin(np.abs(far_strikes - K))
        K_far_nearest = far_strikes[idx_far]
        far_row = df_far[df_far["strike"] == K_far_nearest].iloc[0]

        if K < K_atm_near:
            price_far = far_row["pmid"] if not math.isnan(far_row["pmid"]) else far_row["cmid"]
        else:
            price_far = far_row["cmid"] if not math.isnan(far_row["cmid"]) else far_row["pmid"]

        if math.isnan(price_far) or price_far <= 0:
            continue
        iv_far = bs_iv(price_far, F_far, K_far_nearest, T_far, rfr,
                       is_call=(K >= K_atm_near))
        if iv_far <= 0:
            continue

        # ── Variance interpolation ─────────────────────────────────────────
        var_near = (iv_near / 100.0) ** 2 * T_near
        var_far = (iv_far / 100.0) ** 2 * T_far
        var30 = w1 * var_near + w2 * var_far

        if var30 <= 0:
            continue

        # Convert to 30-day vol (%)
        vol30 = math.sqrt(var30 / T30) * 100.0

        # ── Classify into put vs call skew ─────────────────────────────────
        if K < K_atm_near:
            put_skew_30d[K] = vol30
        else:
            call_skew_30d[K] = vol30

    return put_skew_30d, call_skew_30d

def get_vol_at_strike(skew_dict: dict[float, float], target_strike: float) -> float:
    """
    Linearly interpolate vol at target_strike from a skew dict (strike→vol).
    Returns edge values if target is outside the strike range.
    """
    if not skew_dict:
        return 0.0
    strikes = sorted(skew_dict.keys())
    if target_strike <= strikes[0]:
        return skew_dict[strikes[0]]
    if target_strike >= strikes[-1]:
        return skew_dict[strikes[-1]]
    # Linear interpolation
    for i in range(len(strikes) - 1):
        k_lo, k_hi = strikes[i], strikes[i + 1]
        if k_lo <= target_strike <= k_hi:
            v_lo = skew_dict[k_lo]
            v_hi = skew_dict[k_hi]
            t = (target_strike - k_lo) / (k_hi - k_lo)
            return v_lo + t * (v_hi - v_lo)
    return 0.0

def get_vol_at_strike_from_df(near_df: pd.DataFrame, K: float, F: float,
                                T: float, rfr: float, side: str) -> float:
    """
    Look up the IV (%) at strike K from near-term expiry DataFrame.
    Uses nearest strike if exact strike not available.
    side: 'put' or 'call'
    """
    strikes = near_df["strike"].values
    idx = np.argmin(np.abs(strikes - K))
    K_nearest = strikes[idx]
    row = near_df[near_df["strike"] == K_nearest].iloc[0]
    if side == 'put':
        price = row["pmid"] if not math.isnan(row["pmid"]) else row["cmid"]
    else:
        price = row["cmid"] if not math.isnan(row["cmid"]) else row["pmid"]
    if math.isnan(price) or price <= 0:
        return 0.0
    iv = bs_iv(price, F, K_nearest, T, rfr, is_call=(side == 'call'))
    return iv

def _delta_func(K: float, target_delta: float, S: float,
                near_df: pd.DataFrame, F: float, T_near: float,
                rfr: float, side: str) -> float:
    """
    Objective function for delta-based strike finding.
    Returns (computed_signed_delta - target_delta).
    For puts: signed_delta = N(d1) - 1 (ranges 0 to -1)
    For calls: signed_delta = N(d1) (ranges 0 to 1)
    """
    if K <= 0:
        return float('inf')
    iv_at_K = get_vol_at_strike_from_df(near_df, K, F, T_near, rfr, side)
    if iv_at_K <= 0:
        return float('inf')
    iv_decimal = iv_at_K / 100.0
    sqrt_T = math.sqrt(T_near)
    d1 = (math.log(S / K) + 0.5 * iv_decimal ** 2 * T_near) / (iv_decimal * sqrt_T)
    if side == 'put':
        signed_delta = norm.cdf(d1) - 1.0  # ranges 0 to -1
    else:
        signed_delta = norm.cdf(d1)         # ranges 0 to 1
    return signed_delta - target_delta

def find_strike_for_delta(target_delta: float, S: float,
                          near_df: pd.DataFrame, far_df: pd.DataFrame,
                          dte_near: int, dte_far: int,
                          atm_vol: float, atm_k: float,
                          F: float, rfr: float,
                          side: str = 'put') -> float:
    """
    Find the strike K such that the option's signed delta equals `target_delta`.
    Uses Brent's method with the actual IV from the near-term skew at each evaluation.

    Signed delta convention:
      Puts:  delta = N(d1) - 1  (ranges 0 to -1; ATM = -0.5, 10-delta put = -0.10)
      Calls: delta = N(d1)      (ranges 0 to  1; ATM =  0.5, 10-delta call =  0.10)

    Parameters
    ----------
    target_delta : signed target delta (e.g., -0.10 for 10-delta put, 0.10 for 10-delta call)
    S           : spot price
    near_df     : near-term expiry DataFrame
    far_df      : far-term expiry DataFrame (unused, kept for API compatibility)
    dte_near    : days to expiration for near-term
    dte_far     : days to expiration for far-term (unused)
    atm_vol     : near-term ATM vol in %
    atm_k       : near-term ATM strike (unused)
    F           : forward price
    rfr         : risk-free rate (decimal)
    side        : 'put' or 'call'

    Returns
    -------
    K           : strike price
    """
    T_near = dte_near / 365.0

    # Bracket for Brent's method
    if side == 'put':
        # For puts: K < S (OTM puts have negative delta from -1 to 0)
        # target_delta is negative (e.g., -0.10 for 10-delta put)
        K_lo = 0.5 * S
        K_hi = S
        f_lo = _delta_func(K_lo, target_delta, S, near_df, F, T_near, rfr, side)
        f_hi = _delta_func(K_hi, target_delta, S, near_df, F, T_near, rfr, side)
    else:
        # For calls: K > S (OTM calls have positive delta from 0 to 1)
        # target_delta is positive (e.g., 0.10 for 10-delta call)
        K_lo = S
        K_hi = 2.0 * S
        f_lo = _delta_func(K_lo, target_delta, S, near_df, F, T_near, rfr, side)
        f_hi = _delta_func(K_hi, target_delta, S, near_df, F, T_near, rfr, side)

    # If bracket is invalid, fall back to moneyness-based estimate
    try:
        K_root = brentq(
            lambda K: _delta_func(K, target_delta, S, near_df, F, T_near, rfr, side),
            K_lo, K_hi, xtol=1.0, maxiter=100
        )
        return float(K_root)
    except (ValueError, RuntimeError):
        # Fall back to moneyness formula
        sigma = atm_vol / 100.0
        T = dte_near / 365.0
        if side == 'put':
            d = 1.0 - target_delta  # N⁻¹ argument for puts
            inv = norm.ppf(max(1e-6, min(d, 1 - 1e-6)))
            K = S * math.exp(sigma * math.sqrt(T) * inv + 0.5 * sigma ** 2 * T)
        else:
            inv = norm.ppf(max(1e-6, min(target_delta, 1 - 1e-6)))
            K = S * math.exp(-sigma * math.sqrt(T) * inv + 0.5 * sigma ** 2 * T)
        return max(K, 0.5 * S)

# FACTOR GENERATION
def get_near_term_vol_at_strike(
    near_df: pd.DataFrame, F: float, T: float, rfr: float,
    K_target: float, K_atm_near: float
) -> float:
    """
    Get the raw near-term IV at a specific strike from the near-term expiry chain.
    
    This is the 'vol_old(S_new)' for F1 per the whitepaper (P13, F1 formula):
    "vol_old(S_new) = vol of OLD near-term near-ATM option evaluated at NEW near-term near-ATM strike"
    
    Uses the OLD near-term near-ATM option type (call if K_target >= K_atm_near, 
    put if K_target < K_atm_near) at the K_target strike from the raw near-term chain.
    
    Parameters
    ----------
    near_df     : DataFrame with near-term expiry options (columns: strike, cmid, pmid)
    F           : forward price for near-term
    T           : time to expiration in years for near-term
    rfr         : risk-free rate
    K_target    : strike at which to evaluate IV
    K_atm_near  : near-term ATM strike (used to decide call vs put)
    """
    near_strikes = near_df["strike"].values
    idx = np.argmin(np.abs(near_strikes - K_target))
    K_nearest = near_strikes[idx]
    row = near_df[near_df["strike"] == K_nearest].iloc[0]
    
    # Use call if K >= K_atm (in the money on calls side), else put
    if K_target >= K_atm_near:
        price = row["cmid"] if not math.isnan(row["cmid"]) else row["pmid"]
        is_call = True
    else:
        price = row["pmid"] if not math.isnan(row["pmid"]) else row["cmid"]
        is_call = False
    
    if math.isnan(price) or price <= 0:
        return 0.0
    
    iv = bs_iv(price, F, K_nearest, T, rfr, is_call=is_call)
    return iv

def run_decomposition(prev: dict, curr: dict) -> VIXDecomposition | None:
    """
    Run 6-factor VIX decomposition between two consecutive dates.
    Uses the 30-day interpolated skew methodology from the CBOE whitepaper.

    Methodology (Whitepaper P13-P22):
    - F1: vol_old(S_new) - vol_old(S_old), OLD near-term near-ATM IV at S_new
    - F2: vol_new(S_new) - vol_old(S_new), NEW near-term near-ATM IV at S_new
    - F3: put skew gradient at 30-delta put strike (single-strike approx)
    - F4: call skew gradient at 30-delta call strike (single-strike approx)
    - F5: downside convexity at 10-delta put strike (single-strike approx)
    - F6: upside convexity at 10-delta call strike (single-strike approx)
    """
    # ── Unpack 30d skews ───────────────────────────────────────────────────
    put_old = prev.get("put_skew_30d", {})
    put_new = curr.get("put_skew_30d", {})
    call_old = prev.get("call_skew_30d", {})
    call_new = curr.get("call_skew_30d", {})

    if not put_old or not put_new or not call_old or not call_new:
        return None

    # ── Spot and ATM vols ──────────────────────────────────────────────────
    S_old = prev["spot"]
    S_new = curr["spot"]

    # ATM vol from previous date's near-term ATM strike
    K_atm_old = prev["K_atm1"]
    prev_IV1 = prev["IV1"]
    prev_atm_vol = prev_IV1  # ATM vol from previous date

    # ── F1: Sticky Strike ──────────────────────────────────────────────────
    # F1 = vol_old(S_new) - vol_old(S_old) per whitepaper P13 Eq 4
    # Where "vol_old(S)" = OLD near-term near-ATM IV at strike S
    # This uses the RAW near-term chain IV (not 30d interpolated), from the
    # OLD near-term near-ATM option type (call if K>=K_atm, put if K<K_atm)
    # evaluated at the NEW near-term near-ATM strike.
    vol_old_at_S_new = get_near_term_vol_at_strike(
        prev["chain1_df"], prev["F"], prev["T1"], prev["rfr"],
        S_new, K_atm_old
    )
    F1 = vol_old_at_S_new - prev_atm_vol

    # ── F2: Parallel Shift ─────────────────────────────────────────────────
    # F2 = vol_new(S_new) - vol_old(S_new) per whitepaper P18 Eq 5
    # Both evaluated at the same strike (S_new) on new vs old skews.
    # Uses the NEW near-term near-ATM call/put IV at S_new.
    vol_new_at_S_new = get_near_term_vol_at_strike(
        curr["chain1_df"], curr["F"], curr["T1"], curr["rfr"],
        S_new, curr["K_atm1"]
    )
    F2 = vol_new_at_S_new - vol_old_at_S_new

    # Use current day's near/far data to find strikes for target deltas
    # Signed delta convention: puts = N(d1)-1 (negative), calls = N(d1) (positive)
    curr_near_df = curr["chain1_df"]
    curr_far_df = curr["chain2_df"]
    curr_dte_near = curr["DTE1"]
    curr_dte_far = curr["DTE2"]
    curr_atm_vol = curr["IV1"]
    curr_atm_k = curr["K_atm1"]
    curr_F = curr["F"]
    curr_rfr = curr["rfr"]

    # 30-delta put strike: signed delta = -0.30
    K_put30 = find_strike_for_delta(
        -0.30, S_new,
        curr_near_df, curr_far_df,
        curr_dte_near, curr_dte_far,
        curr_atm_vol, curr_atm_k,
        curr_F, curr_rfr,
        side='put'
    )

    # 30-delta call strike: signed delta = +0.30
    K_call30 = find_strike_for_delta(
        0.30, S_new,
        curr_near_df, curr_far_df,
        curr_dte_near, curr_dte_far,
        curr_atm_vol, curr_atm_k,
        curr_F, curr_rfr,
        side='call'
    )

    # 10-delta put strike: signed delta = -0.10
    K_put10 = find_strike_for_delta(
        -0.10, S_new,
        curr_near_df, curr_far_df,
        curr_dte_near, curr_dte_far,
        curr_atm_vol, curr_atm_k,
        curr_F, curr_rfr,
        side='put'
    )

    # 10-delta call strike: signed delta = +0.10
    K_call10 = find_strike_for_delta(
        0.10, S_new,
        curr_near_df, curr_far_df,
        curr_dte_near, curr_dte_far,
        curr_atm_vol, curr_atm_k,
        curr_F, curr_rfr,
        side='call'
    )

    # F3: Put Skew Gradient (30-delta put strike)
    vol_put30_old = get_vol_at_strike(put_old, K_put30)
    vol_put30_new = get_vol_at_strike(put_new, K_put30)
    F3_raw_put_change = vol_put30_new - vol_put30_old
    F3 = F3_raw_put_change - F2

    # F4: Call Skew Gradient (30-delta call strike)
    vol_call30_old = get_vol_at_strike(call_old, K_call30)
    vol_call30_new = get_vol_at_strike(call_new, K_call30)
    F4_raw_call_change = vol_call30_new - vol_call30_old
    F4 = F4_raw_call_change - F2

    # F5: Downside Convexity (10-delta put strike)
    vol_put10_old = get_vol_at_strike(put_old, K_put10)
    vol_put10_new = get_vol_at_strike(put_new, K_put10)
    F5_raw = vol_put10_new - vol_put10_old
    F5 = F5_raw - F2 - F3

    # F6: Upside Convexity (10-delta call strike)
    vol_call10_old = get_vol_at_strike(call_old, K_call10)
    vol_call10_new = get_vol_at_strike(call_new, K_call10)
    F6_raw = vol_call10_new - vol_call10_old
    F6 = F6_raw - F2 - F4

    # ── VIX change ground truth ────────────────────────────────────────────
    VIX_old = prev.get("vix_computed", 0.0)
    VIX_new = curr.get("vix_computed", 0.0)
    VIX_old_actual = prev.get("vix_actual", VIX_old) or VIX_old
    VIX_new_actual = curr.get("vix_actual", VIX_new) or VIX_new
    total = VIX_new_actual - VIX_old_actual

    return VIXDecomposition(
        total_vix_change=total,
        factor1_sticky_strike=F1,
        factor2_parallel_shift=F2,
        factor3_put_skew_grad=F3,
        factor4_call_skew_grad=F4,
        factor5_downside_conv=F5,
        factor6_upside_conv=F6,
    )

# CBOE DATA
def fetch_cboe_vix_historical():
    """Fetch VIX daily closing values from CBOE CSV."""
    try:
        import urllib.request
        url = ("https://cdn.cboe.com/api/globalbenchmarks/indices/"
               "benchmark-values/VIX_History.csv")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")
        lines = text.strip().split("\n")
        records = {}
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) >= 2:
                date_str = parts[0].strip()
                try:
                    records[date_str] = float(parts[1].strip())
                except ValueError:
                    pass
        return records
    except Exception as e:
        return {}

# MAIN
def main():
    print("Fetching 2026+ PM snapshots from Supabase...")
    snapshots = fetch_snapshots_2026()
    print(f"  Retrieved {len(snapshots)} snapshots\n")

    if not snapshots:
        print("No data found. Exiting.")
        return

    # CBOE historical VIX
    print("Fetching CBOE VIX historical data for validation...")
    cboe_vix = fetch_cboe_vix_historical()
    print(f"  Got {len(cboe_vix)} CBOE VIX records\n")

    # Compute VIX for each snapshot
    results = []
    skipped = 0

    for snap in snapshots:
        snap_date_str = snap["date"]
        snap_date = datetime.strptime(snap_date_str, "%Y-%m-%d").date()
        payload = snap["payload"]

        spot = payload.get("SPX", {}).get("spot")
        rfr_raw = payload.get("rfr", 0.0)
        if rfr_raw is None:
            rfr_raw = 0.0
        rfr = float(rfr_raw) / 100.0  # convert percentage to decimal

        optionchain = payload.get("SPX", {}).get("optionchain", {})
        vix_actual = payload.get("VIX", {}).get("spot") if isinstance(payload.get("VIX"), dict) else None

        if not optionchain or not spot:
            print(f"  Skipping {snap_date_str} -- missing data")
            skipped += 1
            continue

        vix_result = compute_vix_for_snapshot(spot, rfr, optionchain, snap_date)
        if vix_result is None:
            print(f"  Skipping {snap_date_str} -- VIX computation failed")
            skipped += 1
            continue

        # Fill in CBOE actual VIX
        date_key = snap_date_str
        if date_key in cboe_vix:
            vix_result["vix_actual"] = cboe_vix[date_key]
        elif vix_actual is not None:
            vix_result["vix_actual"] = vix_actual

        # ── Build 30-day interpolated skews from near+far expiries ────────
        df_near = vix_result["chain1_df"]
        df_far = vix_result["chain2_df"]
        dte_near = vix_result["DTE1"]
        dte_far = vix_result["DTE2"]
        F_near = vix_result["F"]
        F_far = vix_result["F2"]
        put_skew, call_skew = build_30day_skew(
            df_near, df_far, dte_near, dte_far, spot, F_near, F_far, rfr)
        vix_result["put_skew_30d"] = put_skew
        vix_result["call_skew_30d"] = call_skew

        results.append(vix_result)
        print(f"  {snap_date_str}: SPX={spot:,.2f}, "
              f"VIX_comp={vix_result['vix_computed']:.2f}, "
              f"VIX_actual={vix_result.get('vix_actual', 'N/A')}, "
              f"NearExp={vix_result['near_exp']}({vix_result['DTE1']}d) "
              f"-> IV1={vix_result['IV1']:.2f}%, "
              f"NextExp={vix_result['next_exp']}({vix_result['DTE2']}d) "
              f"-> IV2={vix_result['IV2']:.2f}%")

    print(f"\nProcessed {len(results)} valid dates, {skipped} skipped\n")

    # ── VIX Decomposition (from 2nd date onwards) ──────────────────────────
    decompositions = [None]  # placeholder for index 0 (no prev date)
    for i in range(1, len(results)):
        try:
            decomp = run_decomposition(results[i - 1], results[i])
            decompositions.append(decomp)
            results[i]["decomp"] = decomp
        except Exception as e:
            print(f"  Decomposition failed for {results[i]['date']}: {e}")
            import traceback
            traceback.print_exc()
            decompositions.append(None)

    # ── Build output table ─────────────────────────────────────────────────
    hdr = (f"{'Date':<12} {'SPX_Spot':>10} {'VIX_Comp':>10} "
           f"{'F1':>8} {'F2':>8} {'F3':>8} {'F4':>8} {'F5':>8} {'F6':>8} "
           f"{'VIX_Actual':>10}")
    sep = "=" * len(hdr)

    print(sep)
    print(hdr)
    print(sep)

    table_lines = []
    all_lines = []

    for i, res in enumerate(results):
        decomp = decompositions[i] if i > 0 else None
        date_str = res["date"][:10]
        spx_s = f"{res['spot']:>10,.2f}"
        vix_c = f"{res['vix_computed']:>10.2f}"

        if decomp:
            f1s = f"{decomp.factor1_sticky_strike:>8.2f}"
            f2s = f"{decomp.factor2_parallel_shift:>8.2f}"
            f3s = f"{decomp.factor3_put_skew_grad:>8.2f}"
            f4s = f"{decomp.factor4_call_skew_grad:>8.2f}"
            f5s = f"{decomp.factor5_downside_conv:>8.2f}"
            f6s = f"{decomp.factor6_upside_conv:>8.2f}"
        else:
            f1s = f2s = f3s = f4s = f5s = f6s = f"{'--':>8}"

        va = (f"{res.get('vix_actual', 'N/A'):>10.2f}"
              if res.get("vix_actual") is not None
              else f"{'N/A':>10}")

        line = f"{date_str:<12} {spx_s} {vix_c} {f1s} {f2s} {f3s} {f4s} {f5s} {f6s} {va}"
        table_lines.append(line)
        all_lines.append(line)
        print(line)

    print(sep)

    # ── Save decomposition CSV ─────────────────────────────────────────────
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)
    decomp_csv_path = os.path.join(output_dir, "vix_decomposition.csv")
    decomp_rows = []
    for i, res in enumerate(results):
        decomp = decompositions[i] if i > 0 else None
        row = {
            "date": res["date"][:10],
            "SPX_spot": res["spot"],
            "VIX_computed": res["vix_computed"],
            "VIX_actual": res.get("vix_actual"),
        }
        if decomp:
            row["F1_sticky_strike"] = decomp.factor1_sticky_strike
            row["F2_parallel_shift"] = decomp.factor2_parallel_shift
            row["F3_put_skew_grad"] = decomp.factor3_put_skew_grad
            row["F4_call_skew_grad"] = decomp.factor4_call_skew_grad
            row["F5_downside_conv"] = decomp.factor5_downside_conv
            row["F6_upside_conv"] = decomp.factor6_upside_conv
            row["sum_factors"] = sum([
                decomp.factor1_sticky_strike,
                decomp.factor2_parallel_shift,
                decomp.factor3_put_skew_grad,
                decomp.factor4_call_skew_grad,
                decomp.factor5_downside_conv,
                decomp.factor6_upside_conv,
            ])
        decomp_rows.append(row)
    decomp_df = pd.DataFrame(decomp_rows)
    decomp_df.to_csv(decomp_csv_path, index=False)
    print(f"\nDecomposition CSV saved to {decomp_csv_path}")

    # ── CBOE Comparison Summary ───────────────────────────────────────────
    print("\n" + "=" * 70)
    print("CBOE COMPARISON SUMMARY")
    print("=" * 70)

    valid_pairs = [
        (r["vix_computed"], r["vix_actual"])
        for r in results if r.get("vix_actual") is not None
    ]

    if valid_pairs:
        errors = [comp - actual for comp, actual in valid_pairs]
        abs_errors = [abs(e) for e in errors]
        print(f"  Dates compared:              {len(valid_pairs)}")
        print(f"  Mean error (Comp - Actual): {np.mean(errors):+.3f}")
        print(f"  Mean |error|:               {np.mean(abs_errors):.3f}")
        print(f"  Max |error|:                {max(abs_errors):.3f}")
        print(f"  Min |error|:                {min(abs_errors):.3f}")
        print()
        print(f"  {'Date':<12} {'Computed':>10} {'Actual':>10} {'Error':>10}")
        print("  " + "-" * 44)
        for r in results:
            if r.get("vix_actual") is not None:
                err = r["vix_computed"] - r["vix_actual"]
                print(f"  {r['date'][:10]:<12} {r['vix_computed']:>10.2f} "
                      f"{r['vix_actual']:>10.2f} {err:>+10.3f}")
    else:
        print("  No CBOE actual VIX values available in the dataset.")
        print("  Note: The payload contains VIX spot values from the data source.")
        print("  CBOE live decomposition tool:")
        print("  https://www.cboe.com/en/tradable-products/vix/vix-decomposition/")

    # ── Save to file ───────────────────────────────────────────────────────
    output_path = os.path.join(os.path.dirname(__file__), "vix_results.txt")
    with open(output_path, "w") as f:
        f.write("VIX Analysis Results -- 2026+\n")
        f.write("=" * 100 + "\n")
        f.write(f"Total dates: {len(results)}  |  Skipped: {skipped}\n")
        f.write(f"CBOE VIX historical records: {len(cboe_vix)}\n\n")
        f.write(sep + "\n")
        f.write(hdr + "\n")
        f.write(sep + "\n")
        for line in all_lines:
            f.write(line + "\n")
        f.write(sep + "\n")

        f.write("\n## CBOE Comparison Summary\n")
        f.write("=" * 70 + "\n")
        if valid_pairs:
            f.write(f"Dates compared:              {len(valid_pairs)}\n")
            f.write(f"Mean error (Computed-Actual): {np.mean(errors):+.3f}\n")
            f.write(f"Mean |error|:               {np.mean(abs_errors):.3f}\n")
            f.write(f"Max |error|:                {max(abs_errors):.3f}\n")
            f.write(f"Min |error|:                {min(abs_errors):.3f}\n")
            f.write(f"\n{'Date':<12} {'Computed':>10} {'Actual':>10} {'Error':>10}\n")
            f.write("  " + "-" * 44 + "\n")
            for r in results:
                if r.get("vix_actual") is not None:
                    err = r["vix_computed"] - r["vix_actual"]
                    f.write(f"  {r['date'][:10]:<12} {r['vix_computed']:>10.2f} "
                            f"{r['vix_actual']:>10.2f} {err:>+10.3f}\n")
        else:
            f.write("No CBOE actual VIX values in dataset.\n")
            f.write("CBOE tool: https://www.cboe.com/en/tradable-products/vix/vix-decomposition/\n")

        f.write("\n## Column Descriptions\n")
        f.write("- VIX_Computed: 30-day constant-maturity VIX using 2-expiry linear interpolation\n")
        f.write("- F1 (Sticky Strike):     ATM vol change from SPX spot move holding skew fixed\n")
        f.write("- F2 (Parallel Shift):    Full surface level change at same strike\n")
        f.write("- F3 (Put Skew Gradient): Put shoulder (30-delta) change net of F2\n")
        f.write("- F4 (Call Skew Gradient):Call shoulder (30-delta) change net of F2\n")
        f.write("- F5 (Downside Convexity):Put wing (10-delta) convexity change net of F2+F3\n")
        f.write("- F6 (Upside Convexity):  Call wing (10-delta) convexity change net of F2+F4\n")
        f.write("- VIX_Actual:             CBOE published VIX (or payload VIX spot where available)\n")
        f.write("\n## Methodology Notes\n")
        f.write("- F1-F6 use SINGLE-STRIKE representative approximation (30-delta put/call, 10-delta put/call)\n")
        f.write("  Full F3 requires VIX recomputed after adjusting ALL 15-45 delta put prices\n")
        f.write("  Full F5 requires VIX recomputed after adjusting ALL 1-15 delta put prices\n")
        f.write("- Delta-to-strike: iterative approach using actual IV from near-term skew at each strike\n")
        f.write("- F1 = OLD_30d_put_vol_at_NEW_spot - OLD_ATM_vol (per whitepaper P13)\n")

    print(f"\nResults saved to {output_path}")

    # ── Generate vix_computed_vs_actual.png ─────────────────────────────────
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        dates = [datetime.strptime(r["date"][:10], "%Y-%m-%d") for r in results]

        fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=True)

        # Panel 1: VIX Computed vs Actual
        ax = axes[0]
        vix_comp = [r["vix_computed"] for r in results]
        vix_actual = [r.get("vix_actual") for r in results]
        ax.plot(dates, vix_comp, label="VIX Computed", color="steelblue", lw=1.8)
        valid_actual = [(d, v) for d, v in zip(dates, vix_actual) if v is not None]
        if valid_actual:
            d_vals, v_vals = zip(*valid_actual)
            ax.plot(dates, [r.get("vix_actual") for r in results],
                    label="VIX Actual", color="darkorange", lw=1.2, alpha=0.8)
        ax.set_ylabel("VIX")
        ax.set_title("VIX Computed vs Actual")
        ax.legend()
        ax.grid(alpha=0.3)

        # Panel 2: Decomposition factors F1–F4
        ax = axes[1]
        decomp_dates = dates[1:]  # decomposition starts from index 1
        f1_vals = [decompositions[i].factor1_sticky_strike for i in range(1, len(results))]
        f2_vals = [decompositions[i].factor2_parallel_shift for i in range(1, len(results))]
        f3_vals = [decompositions[i].factor3_put_skew_grad for i in range(1, len(results))]
        f4_vals = [decompositions[i].factor4_call_skew_grad for i in range(1, len(results))]

        ax.plot(decomp_dates, f1_vals, label="F1 Sticky Strike", lw=1.4)
        ax.plot(decomp_dates, f2_vals, label="F2 Parallel Shift", lw=1.4)
        ax.plot(decomp_dates, f3_vals, label="F3 Put Skew Grad", lw=1.4)
        ax.plot(decomp_dates, f4_vals, label="F4 Call Skew Grad", lw=1.4)
        ax.axhline(0, color="black", lw=0.8, ls="--")
        ax.set_ylabel("Vol Change (pts)")
        ax.set_title("Decomposition Factors F1–F4")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)

        # Panel 3: Decomposition factors F5–F6
        ax = axes[2]
        f5_vals = [decompositions[i].factor5_downside_conv for i in range(1, len(results))]
        f6_vals = [decompositions[i].factor6_upside_conv for i in range(1, len(results))]

        ax.plot(decomp_dates, f5_vals, label="F5 Downside Conv", lw=1.4, color="purple")
        ax.plot(decomp_dates, f6_vals, label="F6 Upside Conv", lw=1.4, color="brown")
        ax.axhline(0, color="black", lw=0.8, ls="--")
        ax.set_ylabel("Vol Change (pts)")
        ax.set_title("Decomposition Factors F5–F6")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")

        plt.suptitle("VIX Analysis & Decomposition (2026+)", fontsize=13)
        plt.tight_layout()

        plot_path = os.path.join(output_dir, "vix_computed_vs_actual.png")
        fig.savefig(plot_path, dpi=150, bbox_inches="tight")
        print(f"Chart saved to {plot_path}")
        plt.close()
    except Exception as e:
        print(f"Chart generation failed (non-critical): {e}")

if __name__ == "__main__":
    main()
