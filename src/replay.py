"""
Phase 3 - The spotlight replay (the hero visual).

Draws one pass play as an animated Plotly field. Receivers glow gold; the
size of the glow grows with how open they are (their spotlight score).
Coverage defenders are near-black dots. At the throw we annotate who the ball
went to vs who was actually open.

Run:
  python3 src/replay.py --game 2021101706 --play 1861
  -> writes outputs/replay.html (opens in any browser)
"""

import argparse
import os
import numpy as np
import pandas as pd
import plotly.graph_objects as go

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
TRACKING = os.path.join(DATA, "tracking")
OUT = os.path.join(ROOT, "outputs")

# --- colors / style --------------------------------------------------------
BG = "#06100B"        # page + field background (very dark green)
TURF = "#0E2A1C"      # the playing surface
LINE = "rgba(255,255,255,0.12)"  # faint yard lines
GOLD = "#FFC94A"      # receivers
DEF_FILL = "#020604"  # coverage defenders (near black)
DEF_LINE = "#6F7CFF"  # defender outline (indigo)
QB_COL = "#FFFFFF"    # quarterback
OTHER = "#5A6660"     # dim grey (linemen, rushers)
BALL = "#FFFFFF"      # the football

SNAP_EVENTS = {"ball_snap", "autoevent_ballsnap"}
THROW_EVENTS = {"pass_forward", "autoevent_passforward"}

FIELD_X = 120.0
FIELD_Y = 53.3
GLOW_CLIP = 10.0  # yards: glow stops growing past this much space


def load_play(game_id, play_id):
    """Read one play's tracking + role/name info; flip left-moving plays."""
    trk = pd.read_csv(
        os.path.join(TRACKING, f"tracking_{game_id}.csv"),
        usecols=[
            "gameId", "playId", "nflId", "frameId", "jerseyNumber",
            "team", "playDirection", "x", "y", "event",
        ],
    )
    trk = trk[trk["playId"] == play_id].copy()
    if trk.empty:
        raise SystemExit(f"No tracking for game {game_id} play {play_id}")

    # Flip left-moving plays so every play reads left-to-right when drawn.
    if trk["playDirection"].iloc[0] == "left":
        trk["x"] = FIELD_X - trk["x"]
        trk["y"] = FIELD_Y - trk["y"]

    roles = pd.read_csv(
        os.path.join(DATA, "pffScoutingData.csv"),
        usecols=["gameId", "playId", "nflId", "pff_role"],
    )
    roles = roles[(roles["gameId"] == game_id) & (roles["playId"] == play_id)]
    players = pd.read_csv(
        os.path.join(DATA, "players.csv"),
        usecols=["nflId", "displayName", "officialPosition"],
    )

    trk = trk.merge(roles[["nflId", "pff_role"]], on="nflId", how="left")
    trk = trk.merge(players, on="nflId", how="left")
    return trk


def frame_windows(trk):
    """Return (snap_frame, throw_frame)."""
    ev = trk[["frameId", "event"]].dropna()
    snap = ev[ev["event"].isin(SNAP_EVENTS)]["frameId"].min()
    throw = ev[ev["event"].isin(THROW_EVENTS)]["frameId"].min()
    return int(snap), int(throw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", type=int, required=True)
    ap.add_argument("--play", type=int, required=True)
    args = ap.parse_args()

    trk = load_play(args.game, args.play)
    snap_f, throw_f = frame_windows(trk)

    # spotlight scores (reuse Phase 1 output so the numbers match exactly)
    sl = pd.read_parquet(os.path.join(OUT, "spotlight_frames.parquet"))
    sl = sl[(sl["gameId"] == args.game) & (sl["playId"] == args.play)]
    # map (frameId, nflId) -> spotlight
    sl_map = {(int(r.frameId), int(r.nflId)): r.spotlight for r in sl.itertuples()}

    # throw-frame facts for the annotation
    throw_rows = sl[sl["frameId"] == throw_f]
    tgt_name, tgt_val, open_name, open_val = None, None, None, None
    if not throw_rows.empty:
        open_r = throw_rows.loc[throw_rows["spotlight"].idxmax()]
        open_name, open_val = open_r["name"], open_r["spotlight"]

    # intended receiver from the description ('intended for X')
    import re
    desc = ""
    plays = pd.read_csv(
        os.path.join(DATA, "plays.csv"),
        usecols=["gameId", "playId", "playDescription", "down", "yardsToGo",
                 "possessionTeam", "defensiveTeam"],
    )
    prow = plays[(plays.gameId == args.game) & (plays.playId == args.play)].iloc[0]
    desc = prow["playDescription"]
    m = re.search(r"intended for\s+([A-Z]\.[A-Za-z'\-]+)", desc) \
        or re.search(r"\bto\s+([A-Z]\.[A-Za-z'\-]+)", desc)
    if m and not throw_rows.empty:
        want = m.group(1).rstrip(".").lower()
        def nk(n):
            parts = str(n).split()
            return f"{parts[0][0]}.{parts[-1]}".lower() if len(parts) >= 2 else ""
        hit = throw_rows[throw_rows["name"].map(nk) == want]
        if not hit.empty:
            tgt_name, tgt_val = hit.iloc[0]["name"], hit.iloc[0]["spotlight"]

    games = pd.read_csv(os.path.join(DATA, "games.csv"))
    grow = games[games.gameId == args.game].iloc[0]

    # window: 1 second (10 frames) before the snap to the end of the play
    start_f = max(int(trk["frameId"].min()), snap_f - 10)
    end_f = int(trk["frameId"].max())
    frames_list = list(range(start_f, end_f + 1))

    # ---- build the animation frames --------------------------------------
    def traces_for(fr):
        d = trk[trk["frameId"] == fr]
        recv = d[d["pff_role"] == "Pass Route"]
        cov = d[d["pff_role"] == "Coverage"]
        qb = d[d["pff_role"] == "Pass"]
        others = d[~d["pff_role"].isin(["Pass Route", "Coverage", "Pass"]) & d["nflId"].notna()]
        ball = d[d["team"] == "football"]

        # glow: translucent gold circle sized by spotlight (clip at 10 yds)
        glow_sizes, glow_x, glow_y = [], [], []
        dot_x, dot_y, dot_txt = [], [], []
        for r in recv.itertuples():
            sp = sl_map.get((fr, int(r.nflId)), 0.0)
            sp = min(sp, GLOW_CLIP)
            glow_sizes.append(12 + sp * 7)   # marker px: base + grows with space
            glow_x.append(r.x); glow_y.append(r.y)
            dot_x.append(r.x); dot_y.append(r.y)
            jersey = "" if pd.isna(r.jerseyNumber) else f"#{int(r.jerseyNumber)}"
            dot_txt.append(jersey)

        t = []
        # 1) glow halo
        t.append(go.Scatter(
            x=glow_x, y=glow_y, mode="markers",
            marker=dict(size=glow_sizes, color="rgba(255,201,74,0.22)",
                        line=dict(width=0)),
            hoverinfo="skip", showlegend=False,
        ))
        # 2) coverage defenders
        t.append(go.Scatter(
            x=cov["x"], y=cov["y"], mode="markers",
            marker=dict(size=11, color=DEF_FILL,
                        line=dict(width=2, color=DEF_LINE)),
            hoverinfo="skip", showlegend=False,
        ))
        # 3) other players (dim grey)
        t.append(go.Scatter(
            x=others["x"], y=others["y"], mode="markers",
            marker=dict(size=9, color=OTHER, line=dict(width=0)),
            hoverinfo="skip", showlegend=False,
        ))
        # 4) QB (white)
        t.append(go.Scatter(
            x=qb["x"], y=qb["y"], mode="markers",
            marker=dict(size=11, color=QB_COL, line=dict(width=0)),
            hoverinfo="skip", showlegend=False,
        ))
        # 5) receiver solid dot + jersey label
        t.append(go.Scatter(
            x=dot_x, y=dot_y, mode="markers+text",
            marker=dict(size=12, color=GOLD, line=dict(width=1, color="#1a1200")),
            text=dot_txt, textposition="top center",
            textfont=dict(color=GOLD, size=11), hoverinfo="skip", showlegend=False,
        ))
        # 6) football (small white dot)
        t.append(go.Scatter(
            x=ball["x"], y=ball["y"], mode="markers",
            marker=dict(size=7, color=BALL, line=dict(width=0)),
            hoverinfo="skip", showlegend=False,
        ))
        return t

    init_traces = traces_for(start_f)
    anim_frames = [
        go.Frame(data=traces_for(fr), name=str(fr)) for fr in frames_list
    ]

    # ---- figure shell -----------------------------------------------------
    fig = go.Figure(data=init_traces, frames=anim_frames)

    # field shapes: turf rectangle + yard lines every 5 yards
    shapes = [dict(type="rect", x0=0, y0=0, x1=FIELD_X, y1=FIELD_Y,
                   fillcolor=TURF, line=dict(width=0), layer="below")]
    for xl in range(0, int(FIELD_X) + 1, 5):
        shapes.append(dict(type="line", x0=xl, y0=0, x1=xl, y1=FIELD_Y,
                           line=dict(color=LINE, width=1), layer="below"))

    # throw annotation
    annotations = []
    if tgt_name and open_name:
        annotations.append(dict(
            x=0.5, y=1.02, xref="paper", yref="paper", showarrow=False,
            text=(f"<b>Ball thrown to {tgt_name} ({tgt_val:.1f} yds of space)</b>"
                  f"  while {open_name} had {open_val:.1f} yds"),
            font=dict(color=GOLD, size=13),
        ))

    subtitle = (f"{grow.visitorTeamAbbr} @ {grow.homeTeamAbbr} · Week {grow.week} · "
                f"{int(prow.down)} & {int(prow.yardsToGo)} · {len(frames_list)} frames")

    fig.update_layout(
        title=dict(
            text="The Open Man: see what the QB saw<br>"
                 f"<span style='font-size:13px;color:#9fb0a6'>{subtitle}</span>",
            font=dict(color="#FFFFFF", size=22), x=0.02, xanchor="left",
        ),
        paper_bgcolor=BG, plot_bgcolor=BG,
        shapes=shapes, annotations=annotations,
        xaxis=dict(range=[0, FIELD_X], visible=False, constrain="domain"),
        yaxis=dict(range=[0, FIELD_Y], visible=False,
                   scaleanchor="x", scaleratio=1),  # keep field aspect correct
        margin=dict(l=20, r=20, t=90, b=60),
        height=560,
        updatemenus=[dict(
            type="buttons", showactive=False, x=0.02, y=-0.05, xanchor="left",
            bgcolor="#14281d", font=dict(color="#fff"),
            buttons=[
                dict(label="▶ Play", method="animate",
                     args=[None, dict(frame=dict(duration=100, redraw=True),
                                      fromcurrent=True,
                                      transition=dict(duration=0))]),
                dict(label="⏸ Pause", method="animate",
                     args=[[None], dict(frame=dict(duration=0, redraw=False),
                                        mode="immediate")]),
            ],
        )],
        sliders=[dict(
            active=0, x=0.14, len=0.84, y=-0.04,
            currentvalue=dict(prefix="frame ", font=dict(color="#9fb0a6")),
            steps=[dict(method="animate", label=str(fr),
                        args=[[str(fr)], dict(mode="immediate",
                                              frame=dict(duration=0, redraw=True),
                                              transition=dict(duration=0))])
                   for fr in frames_list],
        )],
    )

    out_path = os.path.join(OUT, "replay.html")
    fig.write_html(out_path, auto_open=False, include_plotlyjs="cdn")
    print(f"snap frame {snap_f}, throw frame {throw_f}, window {start_f}-{end_f}")
    if tgt_name:
        print(f"thrown to {tgt_name} ({tgt_val:.2f}), open man {open_name} ({open_val:.2f})")
    print(f"Replay written -> {out_path}")


if __name__ == "__main__":
    main()
