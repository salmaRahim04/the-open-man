# The Open Man: see what the QB saw

A one-page web experience: a real NFL pass play replays on a night field, each
receiver glows with a gold spotlight sized by his space from the nearest
defender, the play freezes before the throw and asks "Who would you throw to?",
then reveals what the real QB did.

## Data
NFL Big Data Bowl (Next Gen Stats, 2021 season weeks 1-8), from
https://github.com/ThompsonJamesBliss/nfl-big-data-bowl-regional-event-data

```
git clone --depth 1 https://github.com/ThompsonJamesBliss/nfl-big-data-bowl-regional-event-data.git ../bdb-data
ln -s ../bdb-data/data data      # or pass --data ../bdb-data/data
```
`data/` is git-ignored (~826 MB).

## Export a play
```
python3 src/export_play.py --auto                         # best "missed open man" interception
python3 src/export_play.py --game 2021091204 --play 1670  # a specific play
```
Writes `web/showcase.js` (`window.SHOWCASE = {...}`), so the page works by
double-clicking `web/openman.html`, no server needed.
