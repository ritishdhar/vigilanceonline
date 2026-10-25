# Historical evaluation records

These CSVs preserve earlier local experiments, not a portable evaluation dataset or current accuracy certification. `val_manifest.csv` references another machine's dataset paths; edit those paths to point to a separately obtained labeled dataset before running `eval_fight.py`. The videos referenced there are not included. Numerical runtime checks live in `models/regression.json`; current verification results are recorded in the root `validation-results.json`.
