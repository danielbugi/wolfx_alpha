"""Helpers that drive the owner CLI end to end against a seeded disposable schema (explicit imports only; never a conftest name).

The CLI is exercised through its real `main(argv)` and its real `default_connect` -- host / port / dbname / user on argv, the password from a
named environment variable, the disposable schema selected with libpq's `PGOPTIONS` -- so the tests cover the same path an owner runs."""
import io
import json
import os
from pathlib import Path

from conftest import _connect_args
from dataset_world import CAL, CUTOFF, MAT, SHA, TE, TR, VA, config
from research.lab import dataset_cli as CLI
from research.lab import dataset_runner as R

PW_VAR = "LAB_CLI_TEST_PASSWORD"
CODE = R.CodeIdentity(SHA, True)


def spec_doc(**over):
    cfg = config()
    del cfg["universe_members"]
    doc = {"schema": "lab_authoring_spec_v1", "dataset_name": "lab_ds", "dataset_version": "v1", "label_version": "fwd_v1",
           "label_methodology_version": "fwd_v1.m1", "label_horizons": [5, 20, 60], "feature_versions": {"mi_v2": "1"}, "universe_id": "u",
           "knowledge_cutoff_at": CUTOFF.isoformat(), "label_maturity_session": MAT.isoformat(),
           "windows": {"train": [TR[0].isoformat(), TR[1].isoformat()], "validation": [VA[0].isoformat(), VA[1].isoformat()],
                       "test": [TE[0].isoformat(), TE[1].isoformat()]},
           "embargo_sessions": 60, "purge_sessions": 0, "calendar": {"file": "calendar.txt"}, "universe": {"from_db": True}, "config": cfg}
    doc.update(over)
    return doc


def write_spec(dirpath, **over):
    dirpath = Path(dirpath)
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "calendar.txt").write_text("# the trading sessions\n" + "\n".join(d.isoformat() for d in CAL) + "\n", encoding="utf-8")
    path = dirpath / "spec.json"
    path.write_text(json.dumps(spec_doc(**over)), encoding="utf-8")
    return path


class Cli:
    """One database the CLI is pointed at (a schema of the test cluster, or another database of it)."""

    def __init__(self, monkeypatch, *, schema=None, dbname=None):
        args = _connect_args()
        self._mp, self._schema = monkeypatch, schema
        self.dbname = dbname or args["dbname"]
        self.argv_db = ["--host", args["host"], "--port", str(args["port"]), "--dbname", self.dbname, "--user", args["user"], "--password-env", PW_VAR]
        self.password = args["password"]

    def _select(self):
        """The environment is process-global, so each Cli re-selects its own schema and password at the moment it runs."""
        self._mp.setenv(PW_VAR, self.password)
        if self._schema:
            self._mp.setenv("PGOPTIONS", f"-c search_path={self._schema}")
        else:
            self._mp.delenv("PGOPTIONS", raising=False)

    def run(self, *argv, code=CODE, connect=None):
        self._select()
        out, err = io.StringIO(), io.StringIO()
        rc = CLI.main([*argv, *self.argv_db], connect=connect, code_provider=lambda _root: code, out=out.write, err=err.write)
        return rc, out.getvalue(), err.getvalue()


def read_artifacts(out_dir, names=CLI.CANONICAL_ARTIFACTS):
    return {n: (Path(out_dir) / n).read_bytes() for n in names}
