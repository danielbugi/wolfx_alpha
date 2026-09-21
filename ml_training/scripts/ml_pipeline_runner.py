# ml_training/scripts/ml_pipeline_runner.py
"""
ML pipeline runner (rewritten 2026-09-20).

The old chain (breakouts -> momentum_labeler -> feature_builder -> old trainer) mixed three tables computed on
different price bases and is retired -- see CLAUDE.md "ML audit 2026-09-20". The current chain is:

    build   ml_training/data_preparation/build_dataset.py   stock_prices only -> ml_breakout_dataset_v2
    train   ml_training/models/momentum_predictor.py        honest chronological evaluation + promotion gate
    test    pytest ml_training/tests                        unit tests + train/serve feature parity

Usage (repo root):
    python ml_training/scripts/ml_pipeline_runner.py test
    python ml_training/scripts/ml_pipeline_runner.py build [--replace]
    python ml_training/scripts/ml_pipeline_runner.py train [--target momentum|plan_profit] [--no-promote]
    python ml_training/scripts/ml_pipeline_runner.py full        # test -> build --replace -> train (both targets)
"""
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PY = sys.executable


def run(label, args):
    t0 = time.time()
    print(f"\n=== {label} ===", flush=True)
    rc = subprocess.call([PY, *args], cwd=ROOT)
    print(f"=== {label}: {'OK' if rc == 0 else f'FAILED (exit {rc})'} in {time.time() - t0:.0f}s ===", flush=True)
    return rc == 0


def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "full"
    extra = sys.argv[2:]
    build = ["ml_training/data_preparation/build_dataset.py"]
    train = ["ml_training/models/momentum_predictor.py"]

    if cmd == "test":
        ok = run("unit + parity tests", ["-m", "pytest", "ml_training/tests", "-q"])
    elif cmd == "build":
        ok = run("build dataset", build + extra)
    elif cmd == "train":
        ok = run("train + evaluate", train + extra)
    elif cmd == "full":
        ok = (run("tests", ["-m", "pytest", "ml_training/tests", "-q"])
              and run("build dataset (full rebuild)", build + ["--replace"])
              and run("train + evaluate: momentum", train + ["--target", "momentum"])
              and run("train + evaluate: plan_profit", train + ["--target", "plan_profit"]))
    else:
        print(__doc__)
        sys.exit(2)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
