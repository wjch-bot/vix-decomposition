import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import time

# --- Credentials ---
SUPABASE_URL = "https://lfarbnfpqsigxcswevyx.supabase.co"
SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxmYXJibmZwcXNpZ3hjc3dldnl4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc0OTcwNjUzNywiZXhwIjoyMDY1MjgyNTM3fQ.Zthn2o_ZWdDrM3bBQH7qe6KN_SG2ezY9K2Aix0ORe2s"

headers = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Accept-Encoding": "identity"  # no gzip to reduce proxy overhead
}

# --- Paginate through all rows with small batches ---
all_data = []
offset = 0
limit = 10  # small batch to avoid timeout
total_rows = None

while True:
    params = {
        "select": "date, payload",
        "order": "date.asc",
        "limit": limit,
        "offset": offset,
        "period": "eq.PM",
    }

    url = f"{SUPABASE_URL}/rest/v1/market_snapshots"
    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        print(f"Error at offset={offset}: {response.status_code} {response.text[:300]}")
        time.sleep(2)
        continue  # try same offset again

    batch = response.json()
    if isinstance(batch, dict):
        print(f"Response is a dict at offset={offset}: {batch}")
        break

    all_data.extend(batch)
    content_range = response.headers.get('Content-Range', '')

    if total_rows is None and '/' in content_range:
        total_str = content_range.split('/')[-1]
        if total_str != '*':
            total_rows = int(total_str)

    print(f"offset={offset}, fetched={len(batch)}, total={len(all_data)}" +
          (f"/{total_rows}" if total_rows else "") +
          f", range={content_range}")

    if len(batch) < limit:
        break
    offset += limit
    time.sleep(0.25)  # be gentle with the API

print(f"\nTotal rows fetched: {len(all_data)}")

# --- Extract DTE data ---
records = []

for item in all_data:
    snapshot_date_str = item['date']
    snapshot_date = pd.to_datetime(snapshot_date_str).date()

    if 'SPX' not in item['payload']:
        continue

    optionchain = item['payload']['SPX'].get('optionchain', {})
    expiry_dates = [e for e in optionchain.keys() if e != snapshot_date_str]

    for exp_str in expiry_dates:
        exp_date = pd.to_datetime(exp_str).date()
        dte = (exp_date - snapshot_date).days
        if dte < 0:
            continue
        records.append({
            'snapshot_date': snapshot_date,
            'expiry_date': exp_date,
            'dte': dte
        })

df = pd.DataFrame(records)
print(f"Total records: {len(df)}")
if df.empty:
    print("No records found")
    exit(1)
print(f"Snapshot dates: {df['snapshot_date'].min()} to {df['snapshot_date'].max()}")
print(f"Unique snapshot dates: {df['snapshot_date'].nunique()}")
print(f"Unique expiry dates: {df['expiry_date'].nunique()}")

# --- Assign expiry position per snapshot date (1st, 2nd, ...) ---
df['expiry_ordinal'] = df.groupby('snapshot_date')['expiry_date'].transform(
    lambda x: pd.Series(range(1, len(x) + 1), index=x.sort_values().index)
)

# --- Pivot ---
pivot = df.pivot_table(index='snapshot_date', columns='expiry_ordinal', values='dte')
print(f"Pivot shape: {pivot.shape}, max expiry position: {pivot.columns.max()}")

# --- Plot ---
fig, ax = plt.subplots(figsize=(14, 8))
colors = plt.cm.tab10.colors

for pos in pivot.columns:
    col = pivot[pos].dropna()
    if len(col) == 0:
        continue
    ax.plot(col.index, col.values, marker='o', markersize=3,
            linewidth=1.5, label=f"Expiry {pos}",
            color=colors[(pos - 1) % len(colors)])

# X-axis: label every ~3rd date
n_dates = len(pivot.index)
tick_interval = max(1, n_dates // 15)
ax.set_xticks(pivot.index[::tick_interval])
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
plt.xticks(rotation=45, ha='right')

ax.set_yscale('log')
ax.set_xlabel('Snapshot Date')
ax.set_ylabel('DTE (Days to Expiration) — Log Scale')
ax.set_title('SPX/VIX Option Expiration DTE Over Time')
# No legend (removed per user request)
ax.grid(True, which='both', linestyle='--', linewidth=0.5)

plt.tight_layout()
output_path = '/Users/warrenjin/agent-workspace/output/dte_chart.png'
plt.savefig(output_path, dpi=150)
print(f"\nChart saved to {output_path}")
