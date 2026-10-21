"""
Phase 2 - Pick the showcase play.

We want the most painful "open man" moments: an INTERCEPTION where the most
open receiver was NOT the intended one. We rank them by how much space was
left on the table = (most-open spotlight) - (intended receiver spotlight),
biggest gap first.

Interception text does NOT say 'to X' but it DOES say 'intended for X.Lastname'
(185 of 189 INTs in this data). That token is the ground-truth target, so we
read it straight from the play description - no tracking guess needed.

Reads outputs/spotlight_throw.csv (Phase 1 only).

Run:
  python3 src/pick_showcase.py
"""

import os
import re
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "outputs")


def intended_key(desc):
    """Pull 'intended for E.Engram' -> 'e.engram' (lowercase match key)."""
    if not isinstance(desc, str):
        return None
    m = re.search(r"intended for\s+([A-Z]\.[A-Za-z'\-]+)", desc)
    if not m:
        return None
    return m.group(1).strip().rstrip(".").lower()


def name_key(display_name):
    """'Chris Godwin' -> 'c.godwin' (same shape as the description token)."""
    if not isinstance(display_name, str) or not display_name.strip():
        return None
    parts = display_name.strip().split()
    if len(parts) < 2:
        return None
    return f"{parts[0][0]}.{parts[-1]}".lower()


def main():
    t = pd.read_csv(os.path.join(OUT, "spotlight_throw.csv"))
    ins = t[t["passResult"] == "IN"].copy()

    ins["intended_key"] = ins["playDescription"].map(intended_key)
    ins["my_key"] = ins["name"].map(name_key)

    rows = []
    for (gid, pid), g in ins.groupby(["gameId", "playId"]):
        want = g["intended_key"].iloc[0]
        if want is None:
            continue  # the 4 INTs with no named target
        tgt = g[g["my_key"] == want]
        if tgt.empty:
            continue  # intended receiver was not a tracked route runner
        tgt_row = tgt.iloc[0]
        open_row = g.loc[g["spotlight_at_throw"].idxmax()]

        # Only interesting when the open man is NOT the intended receiver.
        if open_row["nflId"] == tgt_row["nflId"]:
            continue

        rows.append(
            {
                "gameId": gid,
                "playId": pid,
                "target_name": tgt_row["name"],
                "target_spotlight": round(tgt_row["spotlight_at_throw"], 2),
                "open_name": open_row["name"],
                "open_spotlight": round(open_row["spotlight_at_throw"], 2),
                "gap": round(
                    open_row["spotlight_at_throw"] - tgt_row["spotlight_at_throw"], 2
                ),
                "playDescription": tgt_row["playDescription"],
            }
        )

    ranked = (
        pd.DataFrame(rows).sort_values("gap", ascending=False).reset_index(drop=True)
    )

    print(f"Interceptions where the open man was ignored: {len(ranked)}\n")
    print("TOP 10 SHOWCASE CANDIDATES (biggest wasted space first)")
    print("=" * 78)
    for i, r in ranked.head(10).iterrows():
        print(f"\n#{i + 1}  gap {r['gap']} yds   game {r['gameId']}  play {r['playId']}")
        print(f"    threw to : {r['target_name']}  ({r['target_spotlight']} yds of space)")
        print(f"    open man : {r['open_name']}  ({r['open_spotlight']} yds of space)")
        print(f"    {r['playDescription']}")

    path = os.path.join(OUT, "showcase_candidates.csv")
    ranked.to_csv(path, index=False)
    print("\n" + "=" * 78)
    print(f"Full ranked list saved -> {path}")


if __name__ == "__main__":
    main()
