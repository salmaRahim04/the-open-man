"""
Phase 4 - Insight charts (dark/gold style).

Chart 1 "Light wins":  does more space = more completions?
  For targeted receivers, bucket spotlight-at-throw and show completion rate.

Chart 2 "Left in the light":  who gets ignored most?
  Receivers who were the MOST OPEN player at the throw but NOT the target.
  Count per player (min 20 routes run), top 10.

Reads outputs/spotlight_throw.csv (Phase 1). Writes HTML + PNG for each chart.

Run:
  python3 src/charts.py
"""

import os
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "outputs")

BG = "#06100B"
TURF_PANEL = "#0E2A1C"
GOLD = "#FFC94A"
GOLD_DIM = "#8a6f2a"
TEXT = "#E7EEE9"
GRID = "rgba(255,255,255,0.08)"


def save(fig, name):
    """Write both an HTML and a PNG (PNG needs kaleido)."""
    html = os.path.join(OUT, f"{name}.html")
    fig.write_html(html, include_plotlyjs="cdn", auto_open=False)
    try:
        fig.write_image(os.path.join(OUT, f"{name}.png"), scale=2,
                        width=1000, height=560)
        png = f"{name}.png"
    except Exception as e:  # kaleido missing / failed
        png = f"(PNG skipped: {e})"
    print(f"  saved {name}.html  +  {png}")


def intended_key(desc):
    """Target key for BOTH normal ('to X') and INT ('intended for X') text."""
    if not isinstance(desc, str):
        return None
    m = re.search(r"\bpass\b.*?\bto\s+([A-Z]\.[A-Za-z'\-]+)", desc) \
        or re.search(r"intended for\s+([A-Z]\.[A-Za-z'\-]+)", desc)
    if not m:
        return None
    return m.group(1).rstrip(".").lower()


def name_key(n):
    parts = str(n).split()
    return f"{parts[0][0]}.{parts[-1]}".lower() if len(parts) >= 2 else None


# ---------------------------------------------------------------------------
def chart1_light_wins(t):
    """Completion rate by how open the TARGETED receiver was at the throw."""
    # Rebuild is_target to also cover interceptions ('intended for').
    t = t.copy()
    t["tkey"] = t["playDescription"].map(intended_key)
    t["mkey"] = t["name"].map(name_key)
    t["is_target2"] = t["tkey"] == t["mkey"]

    tg = t[t["is_target2"]].copy()
    # outcome: only C / I / IN count toward a rate (drop R scrambles etc.)
    tg = tg[tg["passResult"].isin(["C", "I", "IN"])]

    bins = [0, 1, 2, 3, 5, np.inf]
    labels = ["0-1", "1-2", "2-3", "3-5", "5+"]
    tg["bucket"] = pd.cut(tg["spotlight_at_throw"], bins=bins, labels=labels,
                          right=False)

    g = tg.groupby("bucket", observed=True)
    rate = g.apply(lambda d: (d["passResult"] == "C").mean(), include_groups=False)
    n = g.size()

    rate = rate.reindex(labels)
    n = n.reindex(labels).fillna(0).astype(int)

    text = [f"{r*100:.0f}%<br>{int(nn)} plays" if pd.notna(r) else ""
            for r, nn in zip(rate.values, n.values)]

    fig = go.Figure(go.Bar(
        x=labels, y=[0 if pd.isna(v) else v for v in rate.values],
        marker=dict(color=GOLD), text=text, textposition="outside",
        textfont=dict(color=TEXT, size=13),
    ))
    fig.update_layout(
        title=dict(text="Light wins — completion rate by the target's space at the throw",
                   font=dict(color=TEXT, size=20), x=0.03),
        paper_bgcolor=BG, plot_bgcolor=BG,
        xaxis=dict(title="yards of space (spotlight at throw)",
                   color=TEXT, gridcolor=GRID),
        yaxis=dict(title="completion rate", range=[0, 1], tickformat=".0%",
                   color=TEXT, gridcolor=GRID),
        margin=dict(l=70, r=30, t=70, b=60), height=560,
    )
    save(fig, "chart1_light_wins")

    print("\n  Chart 1 numbers (space bucket -> completion rate, plays):")
    for lab in labels:
        r = rate[lab]
        print(f"    {lab:>4} yds : {('%.1f%%' % (r*100)) if pd.notna(r) else '  n/a'}"
              f"  ({n[lab]} plays)")
    return rate, n


def chart2_left_in_light(t):
    """Players most often the MOST OPEN man but not the target."""
    t = t.copy()
    t["tkey"] = t["playDescription"].map(intended_key)
    t["mkey"] = t["name"].map(name_key)
    t["is_target2"] = t["tkey"] == t["mkey"]

    routes = t.groupby(["nflId", "name", "position"]).size().rename("routes")

    ignored = t[t["is_most_open"] & (~t["is_target2"])]
    ign_cnt = ignored.groupby(["nflId", "name", "position"]).size().rename("ignored")

    most_open_total = t[t["is_most_open"]].groupby(
        ["nflId", "name", "position"]).size().rename("most_open_total")

    df = pd.concat([routes, ign_cnt, most_open_total], axis=1).fillna(0)
    df = df[df["routes"] >= 20].copy()  # min 20 routes run
    df["ignored"] = df["ignored"].astype(int)
    df["most_open_total"] = df["most_open_total"].astype(int)
    df = df.sort_values("ignored", ascending=False).head(10).reset_index()

    # horizontal bars, biggest at top
    df = df.iloc[::-1]
    labels = [f"{r['name']} ({r['position']})" for _, r in df.iterrows()]
    text = [f"{int(r['ignored'])} ignored · {int(r['most_open_total'])} times most open"
            for _, r in df.iterrows()]

    fig = go.Figure(go.Bar(
        x=df["ignored"], y=labels, orientation="h",
        marker=dict(color=GOLD), text=text, textposition="auto",
        textfont=dict(color="#1a1200", size=12),
    ))
    fig.update_layout(
        title=dict(text="Left in the light — most open but not thrown to (min 20 routes)",
                   font=dict(color=TEXT, size=20), x=0.03),
        paper_bgcolor=BG, plot_bgcolor=BG,
        xaxis=dict(title="times the most open player at the throw (and ignored)",
                   color=TEXT, gridcolor=GRID),
        yaxis=dict(color=TEXT, gridcolor=GRID),
        margin=dict(l=220, r=30, t=70, b=60), height=560,
    )
    save(fig, "chart2_left_in_light")

    print("\n  Chart 2 numbers (top ignored open men):")
    for _, r in df.iloc[::-1].iterrows():
        print(f"    {r['name']:<22} {r['position']:<3} "
              f"ignored {int(r['ignored'])}x  /  most open {int(r['most_open_total'])}x  "
              f"({int(r['routes'])} routes)")
    return df


def main():
    t = pd.read_csv(os.path.join(OUT, "spotlight_throw.csv"))
    print("Building charts...")
    rate, n = chart1_light_wins(t)
    df = chart2_left_in_light(t)

    print("\n" + "=" * 70)
    print("KEY NUMBERS FOR THE README")
    print("=" * 70)
    lo = rate.get("0-1")
    hi = rate.get("5+")
    if pd.notna(lo) and pd.notna(hi):
        print(f"- Tight (0-1 yd) targets complete {lo*100:.0f}% of the time;")
        print(f"  wide-open (5+ yd) targets complete {hi*100:.0f}%.")
    top = df.iloc[-1]
    print(f"- Most-ignored open man: {top['name']} ({top['position']}) - "
          f"most open but not targeted {int(top['ignored'])} times.")
    print(f"- Total 'most open but ignored' events charted across qualified players.")


if __name__ == "__main__":
    main()
