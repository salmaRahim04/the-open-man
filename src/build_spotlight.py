"""
Phase 1 - Build the SPOTLIGHT base table.

The Open Man: see what the QB saw.

SPOTLIGHT SCORE = distance (yards) from a receiver ("Pass Route" player)
to the nearest coverage defender ("Coverage" player), measured every frame
from the snap to the throw. Bigger = more open.

Outputs:
  outputs/spotlight_frames.parquet  (every receiver, every frame, snap->throw)
  outputs/spotlight_throw.csv       (one row per receiver per play, at the throw)

Run:
  python3 src/build_spotlight.py --games 10     # quick test on 10 games
  python3 src/build_spotlight.py                # all games
"""

import argparse
import os
import re
import sys

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
TRACKING = os.path.join(DATA, "tracking")
OUT = os.path.join(ROOT, "outputs")

# Event names that mark the snap and the throw (two spellings each).
SNAP_EVENTS = {"ball_snap", "autoevent_ballsnap"}
THROW_EVENTS = {"pass_forward", "autoevent_passforward"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def name_key(display_name):
    """Turn 'Chris Godwin' into the 'C.Godwin' shape used in play text.

    First initial + '.' + last word of the name, upper/stripped so that
    'C.Godwin' from the play description can be matched to a roster name.
    """
    if not isinstance(display_name, str) or not display_name.strip():
        return None
    parts = display_name.strip().split()
    if len(parts) < 2:
        return None
    first_initial = parts[0][0]
    last = parts[-1]
    return f"{first_initial}.{last}".lower()


def target_from_description(desc):
    """Pull the targeted receiver token (like 'C.Godwin') from play text.

    The target is the text right after the last ' to ' in the description,
    e.g. '... pass incomplete deep right to C.Godwin.' -> 'c.godwin'.
    Returns a lowercase key, or None if there is no ' to ' part.
    """
    if not isinstance(desc, str):
        return None
    # The throw target is the FIRST 'to X.Lastname' that comes after the word
    # 'pass'. Later 'to ...' chunks (yard lines, tacklers) are ignored, so we
    # match exactly one receiver per play.
    m = re.search(r"\bpass\b.*?\bto\s+([A-Z]\.[A-Za-z'\-]+)", desc)
    if not m:
        return None
    return m.group(1).strip().rstrip(".").lower()


# ---------------------------------------------------------------------------
# Core: process one game file
# ---------------------------------------------------------------------------
def process_game(game_id, plays, roles, players):
    """Return (frames_df, throw_df) for a single game, or (None, None).

    plays   : plays.csv rows for this game (indexed by playId access below)
    roles   : pffScoutingData rows for this game (gameId, playId, nflId, pff_role)
    players : players.csv (nflId -> name, position)
    """
    path = os.path.join(TRACKING, f"tracking_{game_id}.csv")
    if not os.path.exists(path):
        return None, None

    # Only read the columns we need - tracking files are large.
    cols = ["gameId", "playId", "nflId", "frameId", "team", "x", "y", "event"]
    trk = pd.read_csv(path, usecols=cols)

    # --- find the snap and throw frame for each play in this game ----------
    ev = trk[["playId", "frameId", "event"]].dropna(subset=["event"])
    snap = (
        ev[ev["event"].isin(SNAP_EVENTS)]
        .groupby("playId")["frameId"]
        .min()
        .rename("snap_frame")
    )
    throw = (
        ev[ev["event"].isin(THROW_EVENTS)]
        .groupby("playId")["frameId"]
        .min()
        .rename("throw_frame")
    )
    windows = pd.concat([snap, throw], axis=1).dropna()
    # Need a real throw AFTER the snap (skip sacks / scrambles / no throw).
    windows = windows[windows["throw_frame"] >= windows["snap_frame"]]
    if windows.empty:
        return None, None
    windows = windows.reset_index()  # columns: playId, snap_frame, throw_frame

    # --- keep only player rows (drop the football) and tag roles -----------
    pl = trk[trk["nflId"].notna()].copy()
    pl["nflId"] = pl["nflId"].astype("int64")
    pl = pl.merge(
        roles[["playId", "nflId", "pff_role"]], on=["playId", "nflId"], how="left"
    )

    # Keep only plays that have a snap->throw window, and only frames inside it.
    pl = pl.merge(windows, on="playId", how="inner")
    pl = pl[
        (pl["frameId"] >= pl["snap_frame"]) & (pl["frameId"] <= pl["throw_frame"])
    ]
    if pl.empty:
        return None, None

    receivers = pl[pl["pff_role"] == "Pass Route"].copy()
    defenders = pl[pl["pff_role"] == "Coverage"].copy()
    if receivers.empty or defenders.empty:
        return None, None

    # --- vectorized nearest-defender distance ------------------------------
    # Join every receiver to every defender in the SAME play+frame, then
    # take the minimum distance per receiver per frame. No python loops.
    key = ["gameId", "playId", "frameId"]
    pair = receivers.merge(
        defenders[key + ["x", "y"]],
        on=key,
        how="inner",
        suffixes=("", "_def"),
    )
    dx = pair["x"] - pair["x_def"]
    dy = pair["y"] - pair["y_def"]
    pair["dist"] = np.sqrt(dx * dx + dy * dy)

    spotlight = (
        pair.groupby(key + ["nflId", "snap_frame", "throw_frame"])["dist"]
        .min()
        .reset_index()
        .rename(columns={"dist": "spotlight"})
    )

    # seconds since snap (10 frames / second)
    spotlight["t"] = (spotlight["frameId"] - spotlight["snap_frame"]) / 10.0

    # attach name + position
    spotlight = spotlight.merge(
        players[["nflId", "displayName", "officialPosition"]],
        on="nflId",
        how="left",
    ).rename(columns={"displayName": "name", "officialPosition": "position"})

    frames_df = spotlight[
        ["gameId", "playId", "frameId", "t", "nflId", "name", "position", "spotlight"]
    ].copy()

    # --- throw-frame table -------------------------------------------------
    at_throw = spotlight[spotlight["frameId"] == spotlight["throw_frame"]].copy()
    if at_throw.empty:
        return frames_df, None

    # most open receiver on each play = largest spotlight at the throw
    at_throw["is_most_open"] = (
        at_throw["spotlight"]
        == at_throw.groupby("playId")["spotlight"].transform("max")
    )

    # join play info (description + result) and figure out the target
    play_info = plays[["playId", "passResult", "playDescription"]].copy()
    at_throw = at_throw.merge(play_info, on="playId", how="left")

    # target key per play from the description
    at_throw["target_key"] = at_throw["playDescription"].map(target_from_description)
    at_throw["my_key"] = at_throw["name"].map(name_key)
    at_throw["is_target"] = at_throw["target_key"] == at_throw["my_key"]

    throw_df = at_throw[
        [
            "gameId",
            "playId",
            "nflId",
            "name",
            "position",
            "spotlight",
            "is_target",
            "is_most_open",
            "passResult",
            "playDescription",
        ]
    ].rename(columns={"spotlight": "spotlight_at_throw"})

    return frames_df, throw_df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Build the spotlight base table.")
    ap.add_argument(
        "--games",
        type=int,
        default=None,
        help="process only the first N games (default: all).",
    )
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)

    # Load the small reference tables once.
    games = pd.read_csv(os.path.join(DATA, "games.csv"))
    plays_all = pd.read_csv(
        os.path.join(DATA, "plays.csv"),
        usecols=["gameId", "playId", "playDescription", "passResult"],
    )
    players = pd.read_csv(
        os.path.join(DATA, "players.csv"),
        usecols=["nflId", "displayName", "officialPosition"],
    )
    roles_all = pd.read_csv(
        os.path.join(DATA, "pffScoutingData.csv"),
        usecols=["gameId", "playId", "nflId", "pff_role"],
    )

    game_ids = games["gameId"].tolist()
    if args.games is not None:
        game_ids = game_ids[: args.games]

    all_frames = []
    all_throws = []
    plays_processed = 0

    for i, gid in enumerate(game_ids, 1):
        plays = plays_all[plays_all["gameId"] == gid]
        roles = roles_all[roles_all["gameId"] == gid]
        frames_df, throw_df = process_game(gid, plays, roles, players)
        if frames_df is not None:
            all_frames.append(frames_df)
            plays_processed += frames_df["playId"].nunique()
        if throw_df is not None:
            all_throws.append(throw_df)
        print(f"  [{i}/{len(game_ids)}] game {gid} done", flush=True)

    if not all_frames:
        print("No plays produced any frames - nothing to write.")
        sys.exit(1)

    frames = pd.concat(all_frames, ignore_index=True)
    throws = pd.concat(all_throws, ignore_index=True)

    frames_path = os.path.join(OUT, "spotlight_frames.parquet")
    throw_path = os.path.join(OUT, "spotlight_throw.csv")
    frames.to_parquet(frames_path, index=False)
    throws.to_csv(throw_path, index=False)

    # Count distinct plays by (gameId, playId) - playId alone repeats across games.
    n_plays = throws[["gameId", "playId"]].drop_duplicates().shape[0]
    print("-" * 60)
    print(f"Plays processed : {plays_processed}")
    print(f"Frame rows      : {len(frames):,}  -> {frames_path}")
    print(f"Throw rows      : {len(throws):,}  -> {throw_path}")
    print(f"Targets matched : {int(throws['is_target'].sum())} of {n_plays} plays")


if __name__ == "__main__":
    main()
