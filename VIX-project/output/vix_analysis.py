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

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

def load_env():
    path = os.path.join(os.path.dirname(__file__), ".env")
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

# ─────────────────────────────────────────────────────────────────────────────
# BLACK-SCHOLES IV (copied from /tmp/tastytrade-bot/methods.py)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# VIX COMPUTATION — CORE
# ─────────────────────────────────────────────────────────────────────────────

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
        chain1_df, chain2_df, F1, F2, T1, T2, vix_actual
    """
    # ── Find two closest expiries to 30 days ────────────────────────────────
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
        "F1": F1,
        "F2": F2,
        "T1": T1,
        "T2": T2,
        "vix_actual": None,   # filled in later
    }


# ─────────────────────────────────────────────────────────────────────────────
# SURFACE HELPERS FOR DECOMPOSITION
# ─────────────────────────────────────────────────────────────────────────────

def moneyness_from_delta(delta: float, vol_pct: float, T_days: int) -> float:
    """Return K/S moneyness for a given put delta (delta < 0.5), vol%, T_days."""
    sigma = vol_pct / 100.0
    T = T_days / 365.0
    d = 1.0 - delta   # put-delta symmetry
    d = max(1e-6, min(1.0 - 1e-6, d))
    inv = norm.ppf(d)
    return math.exp(sigma * math.sqrt(T) * inv + 0.5 * sigma**2 * T)


def get_vol_at_strike_from_chain(chain_df: pd.DataFrame, F: float,
                                  rfr: float, T: float,
                                  K_target: float) -> float:
    """
    Interpolate IV at K_target from a chain DataFrame that has strike, cmid, pmid.
    Uses the IV computed via BS at each available strike, then linear interpolates.
    """
    df = chain_df.copy()
    df = df.sort_values("strike").reset_index(drop=True)

    # Compute IV at each strike
    iv_list = []
    for _, row in df.iterrows():
        K = row["strike"]
        cmid = row["cmid"] if not math.isnan(row["cmid"]) else 0.0
        pmid = row["pmid"] if not math.isnan(row["pmid"]) else 0.0
        if cmid > 1e-6:
            iv = bs_iv(cmid, F, K, T, rfr, is_call=True)
        elif pmid > 1e-6:
            iv = bs_iv(pmid, F, K, T, rfr, is_call=False)
        else:
            iv = float("nan")
        iv_list.append(iv)

    df["iv"] = iv_list
    df = df.dropna(subset=["iv"])

    if df.empty or len(df) < 2:
        return 0.0

    strikes = df["strike"].values
    ivs = df["iv"].values

    if K_target <= strikes[0]:
        return float(ivs[0])
    if K_target >= strikes[-1]:
        return float(ivs[-1])
    return float(np.interp(K_target, strikes, ivs))


def run_decomposition(prev: dict, curr: dict) -> VIXDecomposition | None:
    """
    Run 6-factor VIX decomposition between two consecutive dates.
    prev and curr are outputs from compute_vix_for_snapshot.
    """
    S_old = prev["spot"]
    S_new = curr["spot"]
    vol_old = prev["IV_30d"]
    vol_new = curr["IV_30d"]
    VIX_old = prev.get("vix_computed", 0)
    VIX_new = curr.get("vix_computed", 0)
    VIX_old_actual = prev.get("vix_actual", VIX_old) or VIX_old
    VIX_new_actual = curr.get("vix_actual", VIX_new) or VIX_new

    # Use near-expiry chain (DTE1 is closest to 30)
    chain1_old = prev["chain1_df"]
    chain1_new = curr["chain1_df"]
    dte1_old = prev["DTE1"]
    dte1_new = curr["DTE1"]
    rfr_old = prev["rfr"]
    rfr_new = curr["rfr"]
    F1_old = prev["F1"]
    F1_new = curr["F1"]
    T1_old = prev["T1"]
    T1_new = curr["T1"]

    K_atm_old = prev["K_atm1"]
    K_atm_new = curr["K_atm1"]

    # F1: vol at new ATM strike from old skew
    vol_at_K_new_from_old = get_vol_at_strike_from_chain(
        chain1_old, F1_old, rfr_old, T1_old, S_new)
    # F2: vol at new ATM strike from new skew
    vol_at_K_new_from_new = get_vol_at_strike_from_chain(
        chain1_new, F1_new, rfr_new, T1_new, S_new)

    # Shoulder and wing strikes (using 30d delta conventions)
    K_put_shoulder_old = S_old * moneyness_from_delta(0.30, vol_old, dte1_old)
    K_put_shoulder_new = S_new * moneyness_from_delta(0.30, vol_new, dte1_new)
    K_put_wing_old = S_old * moneyness_from_delta(0.10, vol_old, dte1_old)
    K_put_wing_new = S_new * moneyness_from_delta(0.10, vol_new, dte1_new)
    K_call_shoulder_old = S_old * moneyness_from_delta(0.30, vol_old, dte1_old)
    K_call_shoulder_new = S_new * moneyness_from_delta(0.30, vol_new, dte1_new)
    K_call_wing_old = S_old * moneyness_from_delta(0.10, vol_old, dte1_old)
    K_call_wing_new = S_new * moneyness_from_delta(0.10, vol_new, dte1_new)

    vol_put_shoulder_old = get_vol_at_strike_from_chain(
        chain1_old, F1_old, rfr_old, T1_old, K_put_shoulder_old)
    vol_put_shoulder_new = get_vol_at_strike_from_chain(
        chain1_new, F1_new, rfr_new, T1_new, K_put_shoulder_new)
    vol_put_wing_old = get_vol_at_strike_from_chain(
        chain1_old, F1_old, rfr_old, T1_old, K_put_wing_old)
    vol_put_wing_new = get_vol_at_strike_from_chain(
        chain1_new, F1_new, rfr_new, T1_new, K_put_wing_new)
    vol_call_shoulder_old = get_vol_at_strike_from_chain(
        chain1_old, F1_old, rfr_old, T1_old, K_call_shoulder_old)
    vol_call_shoulder_new = get_vol_at_strike_from_chain(
        chain1_new, F1_new, rfr_new, T1_new, K_call_shoulder_new)
    vol_call_wing_old = get_vol_at_strike_from_chain(
        chain1_old, F1_old, rfr_old, T1_old, K_call_wing_old)
    vol_call_wing_new = get_vol_at_strike_from_chain(
        chain1_new, F1_new, rfr_new, T1_new, K_call_wing_new)

    return decompose_vix_manual(
        S_old=S_old, S_new=S_new,
        vol_old=vol_old, vol_new=vol_new,
        VIX_old=VIX_old_actual, VIX_new=VIX_new_actual,
        K_atm_old=K_atm_old,
        vol_at_K_new_from_old=vol_at_K_new_from_old,
        vol_at_K_new_from_new=vol_at_K_new_from_new,
        K_put_shoulder_old=K_put_shoulder_old,
        vol_put_shoulder_old=vol_put_shoulder_old,
        vol_put_shoulder_new=vol_put_shoulder_new,
        K_put_wing_old=K_put_wing_old,
        vol_put_wing_old=vol_put_wing_old,
        vol_put_wing_new=vol_put_wing_new,
        K_call_shoulder_old=K_call_shoulder_old,
        vol_call_shoulder_old=vol_call_shoulder_old,
        vol_call_shoulder_new=vol_call_shoulder_new,
        K_call_wing_old=K_call_wing_old,
        vol_call_wing_old=vol_call_wing_old,
        vol_call_wing_new=vol_call_wing_new,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CBOE VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

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

    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
