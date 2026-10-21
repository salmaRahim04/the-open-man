"""
The Open Man - export one NFL pass play for the web page.

Spotlight = distance (yards) from a receiver to the nearest "Coverage" defender,
computed for every frame.

Usage:
  python3 src/export_play.py --auto                        # find the best "missed open man" interception
  python3 src/export_play.py --auto --top 5               # the 5 best plays (game mode)
  python3 src/export_play.py --game 2021090900 --play 97   # export a specific play
  (add --data <folder> if the data is not in ./data)

Output: web/showcase.js  ->  window.SHOWCASES = [{...}, ...]; window.SHOWCASE = SHOWCASES[0];
(a .js file instead of JSON so the page works when opened by double-click)
"""
import argparse
import json
import os
import re
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_EVENTS = {"ball_snap", "autoevent_ballsnap"}
THROW_EVENTS = {"pass_forward", "autoevent_passforward"}
FRAMES_BEFORE_SNAP = 10
FRAMES_AFTER_THROW = 15


# ---------------------------------------------------------------- loading
def load_meta(data_dir):
    """Load the small tables (plays, games, players, PFF roles)."""
    plays = pd.read_csv(os.path.join(data_dir, "plays.csv"))
    games = pd.read_csv(os.path.join(data_dir, "games.csv"))
    players = pd.read_csv(os.path.join(data_dir, "players.csv"))
    pff = pd.read_csv(os.path.join(data_dir, "pffScoutingData.csv"),
                      usecols=["gameId", "playId", "nflId", "pff_role"])
    return plays, games, players, pff


def load_tracking(data_dir, game_id, play_ids=None):
    """Load one game's tracking file, flipped so offense always goes right."""
    path = os.path.join(data_dir, "tracking", f"tracking_{game_id}.csv")
    cols = ["gameId", "playId", "nflId", "frameId", "jerseyNumber", "team",
            "playDirection", "x", "y", "s", "o", "event"]
    tr = pd.read_csv(path, usecols=cols)
    if play_ids is not None:
        tr = tr[tr["playId"].isin(play_ids)]
    left = tr["playDirection"] == "left"
    tr.loc[left, "x"] = 120 - tr.loc[left, "x"]
    tr.loc[left, "y"] = 53.3 - tr.loc[left, "y"]
    tr.loc[left, "o"] = (tr.loc[left, "o"] + 180) % 360  # facing angle flips too
    return tr


# ---------------------------------------------------------------- core logic
def key_frames(tr_play):
    """Return (snapFrame, throwFrame) for one play, or None if missing."""
    ev = tr_play.drop_duplicates("frameId")[["frameId", "event"]]
    snap = ev.loc[ev["event"].isin(SNAP_EVENTS), "frameId"]
    throw = ev.loc[ev["event"].isin(THROW_EVENTS), "frameId"]
    if snap.empty or throw.empty:
        return None
    return int(snap.min()), int(throw.min())


def add_spotlight(tr, roles):
    """
    Vectorized: for every receiver at every frame, distance to the nearest
    Coverage defender. Returns tr with a 'spotlight' column (NaN for non-receivers).
    """
    tr = tr.merge(roles, on=["gameId", "playId", "nflId"], how="left")
    rec = tr.loc[tr["pff_role"] == "Pass Route", ["gameId", "playId", "frameId", "nflId", "x", "y"]]
    cov = tr.loc[tr["pff_role"] == "Coverage", ["gameId", "playId", "frameId", "x", "y"]]
    # Cross join receivers x defenders within the same frame, keep the minimum distance
    pairs = rec.merge(cov, on=["gameId", "playId", "frameId"], suffixes=("", "_d"))
    pairs["dist"] = np.hypot(pairs["x"] - pairs["x_d"], pairs["y"] - pairs["y_d"])
    nearest = pairs.groupby(["gameId", "playId", "frameId", "nflId"], as_index=False)["dist"].min()
    nearest = nearest.rename(columns={"dist": "spotlight"})
    return tr.merge(nearest, on=["gameId", "playId", "frameId", "nflId"], how="left")


def short_name(full):
    """'Chris Godwin' -> 'C.Godwin' (first initial + last name)."""
    parts = str(full).split()
    return f"{parts[0][0]}.{' '.join(parts[1:])}" if len(parts) > 1 else str(full)


def find_target(description, receivers):
    """
    Target is only in the text: '... pass short left to C.Godwin ...'.
    receivers: DataFrame with nflId, displayName. Returns nflId or None.
    """
    # Completions say "to C.Godwin", interceptions say "intended for C.Lamb".
    # Names like "Ju.Reid" or "A.St. Brown" exist, so allow a short prefix + last name.
    cands = re.findall(r"(?: to |intended for )([A-Z][a-z]{0,2})\.\s?([A-Z][A-Za-z'\-]+(?:\.\s?[A-Z][A-Za-z'\-]+)?)",
                       str(description))
    for first, last in cands:  # first candidate that is one of the play's receivers wins
        for _, r in receivers.iterrows():
            parts = str(r["displayName"]).split()
            if len(parts) < 2:
                continue
            full_last = " ".join(parts[1:]).replace(" ", "").lower()
            if parts[0].lower().startswith(first.lower()) and \
               full_last.startswith(last.replace(" ", "").lower()):
                return int(r["nflId"])
    return None


def analyze_play(tr_play, desc, players):
    """Compute key frames, target and most open receiver for one play (or None)."""
    kf = key_frames(tr_play)
    if kf is None:
        return None
    snap, throw = kf
    at_throw = tr_play[(tr_play["frameId"] == throw) & tr_play["spotlight"].notna()]
    if at_throw.empty:
        return None
    rec = at_throw.merge(players[["nflId", "displayName"]], on="nflId", how="left")
    target = find_target(desc, rec)
    best = rec.loc[rec["spotlight"].idxmax()]
    out = {"snap": snap, "throw": throw,
           "mostOpen": {"nflId": int(best["nflId"]), "name": best["displayName"],
                        "spotlight": round(float(best["spotlight"]), 2)},
           "target": None}
    if target is not None:
        t = rec[rec["nflId"] == target].iloc[0]
        out["target"] = {"nflId": target, "name": t["displayName"],
                         "spotlight": round(float(t["spotlight"]), 2)}
    return out


# ---------------------------------------------------------------- export
def role_of(pff_role, team):
    if team == "football":
        return "ball"
    return {"Pass": "qb", "Pass Route": "receiver", "Coverage": "defender"}.get(pff_role, "other")


def build(data_dir, game_id, play_id, meta):
    """Build the JSON dict for one play."""
    plays, games, players, pff = meta
    play = plays[(plays["gameId"] == game_id) & (plays["playId"] == play_id)]
    if play.empty:
        sys.exit(f"Play {game_id}/{play_id} not found in plays.csv")
    play = play.iloc[0]
    game = games[games["gameId"] == game_id].iloc[0]

    tr = load_tracking(data_dir, game_id, [play_id])
    tr = add_spotlight(tr, pff[(pff["gameId"] == game_id) & (pff["playId"] == play_id)])
    info = analyze_play(tr, play["playDescription"], players)
    if info is None:
        sys.exit("No snap/throw events (or no receivers) for this play.")

    # Frame window: 10 before snap -> 15 after throw (or last frame)
    first = max(int(tr["frameId"].min()), info["snap"] - FRAMES_BEFORE_SNAP)
    last = min(int(tr["frameId"].max()), info["throw"] + FRAMES_AFTER_THROW)
    tr = tr[(tr["frameId"] >= first) & (tr["frameId"] <= last)]
    tr = tr.merge(players[["nflId", "displayName", "officialPosition"]], on="nflId", how="left")

    out_players = []
    for (nid, team), g in tr.groupby([tr["nflId"].fillna(-1), "team"], sort=False):
        g = g.sort_values("frameId")
        row = g.iloc[0]
        role = role_of(row["pff_role"], team)
        out_players.append({
            "nflId": None if role == "ball" else int(nid),
            "name": "Football" if role == "ball" else row["displayName"],
            "jersey": None if pd.isna(row["jerseyNumber"]) else int(row["jerseyNumber"]),
            "position": "" if pd.isna(row["officialPosition"]) else row["officialPosition"],
            "role": role,
            "frames": [dict({"f": int(f), "x": round(float(x), 2), "y": round(float(y), 2),
                        "spotlight": None if role != "receiver" or pd.isna(s) else round(float(s), 2)},
                        # QB only: facing angle (deg, 0 = +y, 90 = downfield) for the vision cone
                        **({"o": None if pd.isna(o) else round(float(o), 1)} if role == "qb" else {}))
                       for f, x, y, s, o in zip(g["frameId"], g["x"], g["y"], g["spotlight"], g["o"])],
        })

    showcase = {
        "gameId": int(game_id), "playId": int(play_id),
        "description": play["playDescription"],
        "passResult": play["passResult"],
        "offense": play["possessionTeam"], "defense": play["defensiveTeam"],
        "week": int(game["week"]), "down": int(play["down"]), "yardsToGo": int(play["yardsToGo"]),
        "quarter": int(play["quarter"]), "gameClock": play["gameClock"],
        "snapFrame": info["snap"], "throwFrame": info["throw"],
        "target": info["target"], "mostOpen": info["mostOpen"],
        "players": out_players,
    }
    print(f"Built {game_id}/{play_id} ({last - first + 1} frames, {len(out_players)} tracks)")
    return showcase


def write(showcases, out_path):
    """Write all plays to one .js file (loaded with a <script> tag, no server needed)."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write("// Generated by src/export_play.py - do not edit by hand\n")
        fh.write("window.SHOWCASES = " + json.dumps(showcases, separators=(",", ":")) + ";\n")
        fh.write("window.SHOWCASE = window.SHOWCASES[0];\n")
    print(f"\nWrote {len(showcases)} play(s) -> {out_path}")


# ---------------------------------------------------------------- auto mode
def auto(data_dir, meta, n_games=20, top=1):
    """Find interceptions where the most open receiver at the throw was NOT the target."""
    plays, games, players, pff = meta
    ints = plays[plays["passResult"] == "IN"]
    game_ids = sorted(games["gameId"])[:n_games]
    found = []
    for gid in game_ids:
        gp = ints[ints["gameId"] == gid]
        if gp.empty:
            continue
        tr = load_tracking(data_dir, gid, list(gp["playId"]))
        tr = add_spotlight(tr, pff[pff["gameId"] == gid])
        for _, p in gp.iterrows():
            info = analyze_play(tr[tr["playId"] == p["playId"]], p["playDescription"], players)
            if not info or not info["target"] or info["target"]["nflId"] == info["mostOpen"]["nflId"]:
                continue
            gap = info["mostOpen"]["spotlight"] - info["target"]["spotlight"]
            found.append((gap, gid, int(p["playId"]), p["playDescription"], info))
    found.sort(key=lambda r: -r[0])
    print(f"Scanned {len(game_ids)} games, {len(found)} candidate interceptions.\n")
    for gap, gid, pid, desc, info in found[:5]:
        print(f"game {gid} play {pid}  gap {gap:.1f} yds")
        print(f"  {desc}")
        print(f"  most open: {info['mostOpen']['name']} ({info['mostOpen']['spotlight']} yds)"
              f"  | target: {info['target']['name']} ({info['target']['spotlight']} yds)\n")
    if not found:
        sys.exit("No candidates found.")
    return [(gid, pid) for _, gid, pid, _, _ in found[:top]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(ROOT, "data"), help="folder with the CSV files")
    ap.add_argument("--auto", action="store_true", help="find and export the best showcase play")
    ap.add_argument("--game", type=int)
    ap.add_argument("--play", type=int)
    ap.add_argument("--top", type=int, default=1, help="with --auto: how many plays to export")
    ap.add_argument("--out", default=os.path.join(ROOT, "web", "showcase.js"))
    a = ap.parse_args()

    meta = load_meta(a.data)
    if a.auto:
        picks = auto(a.data, meta, top=a.top)
    elif a.game and a.play:
        picks = [(a.game, a.play)]
    else:
        ap.error("use --auto or --game <id> --play <id>")
    write([build(a.data, gid, pid, meta) for gid, pid in picks], a.out)


if __name__ == "__main__":
    main()
