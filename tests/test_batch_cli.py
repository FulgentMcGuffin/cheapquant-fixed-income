"""Tests for the batch_bond_analytics CLI: argument parsing and --no-gui wiring."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from cqfi.batch import cli as batch_cli
from cqfi.batch.future_planner import FutureBatchPlan, FutureSeriesPlan, FutureWorkItem
from cqfi.batch.planner import BatchPlan, IssuerPlan, WorkItem
from cqfi.bond_futures import BOND_FUTURE_CONVENTIONS, BondFuture
from cqfi.config import AppSettings
from cqfi.delivery_basket import BasketMember, DeliveryBasket
from cqfi.instruments import Bond


def test_parser_accepts_multiple_issuers_and_dates():
    parser = batch_cli.build_arg_parser()
    args, extra = parser.parse_known_args(
        ["--issuer", "FRA", "ita", "--start", "2020-01-01", "--end", "2020-12-31"]
    )
    assert args.issuer == ["FRA", "ita"]
    assert args.future is None
    assert args.delivery == "HMUZ"
    assert args.start == date(2020, 1, 1)
    assert args.end == date(2020, 12, 31)
    assert args.curve_label == "BOND_ZERO"
    assert args.no_gui is False
    assert extra == []


def test_parser_accepts_future_mode():
    parser = batch_cli.build_arg_parser()
    args, extra = parser.parse_known_args(
        [
            "--future", "FGBX", "FGBM", "--delivery", "HMUZ",
            "--start", "2020-01-01", "--end", "2020-12-31",
        ]
    )
    assert args.future == ["FGBX", "FGBM"]
    assert args.issuer is None
    assert args.delivery == "HMUZ"
    assert extra == []


def test_parser_rejects_issuer_and_future_together():
    parser = batch_cli.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_known_args(
            [
                "--issuer", "FRA", "--future", "FGBM",
                "--start", "2020-01-01", "--end", "2020-12-31",
            ]
        )


def test_parser_requires_issuer_or_future():
    parser = batch_cli.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_known_args(["--start", "2020-01-01", "--end", "2020-12-31"])


def test_parser_rejects_bad_date():
    parser = batch_cli.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_known_args(
            ["--issuer", "FRA", "--start", "not-a-date", "--end", "2020-01-31"]
        )


def test_main_rejects_end_before_start():
    with pytest.raises(SystemExit):
        batch_cli.main(["--issuer", "FRA", "--start", "2020-02-01", "--end", "2020-01-01"])


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        config_path=tmp_path / "cqfi.yaml",
        ycs_db_path=tmp_path / "ycs.sqlite",
        ycs_semantics_dir=tmp_path,
        bond_analytics_db_path=tmp_path / "bond_analytics.sqlite",
        bond_analytics_semantics_dir=Path("semantics"),
        bond_analytics_semantics_path=Path("semantics/bond_analytics.yaml"),
        quant_cache_db_path=tmp_path / "quant_cache.sqlite",
        quant_cache_semantics_dir=Path("semantics"),
        quant_cache_semantics_path=Path("semantics/quant_cache.yaml"),
        sessions_dir=tmp_path / "sessions",
        mcp_datasets={},
    )


def test_main_no_gui_short_circuits_on_empty_plan(monkeypatch, tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(batch_cli, "load_settings", lambda config: settings)

    empty_plan = BatchPlan(issuer_plans=())
    monkeypatch.setattr(batch_cli, "build_plan", lambda *a, **k: empty_plan)

    called = {}
    monkeypatch.setattr(
        batch_cli.BatchEngine, "run", lambda self, *a, **k: called.setdefault("ran", True)
    )

    exit_code = batch_cli.main(
        ["--issuer", "fra", "--start", "2020-01-01", "--end", "2020-01-31", "--no-gui"]
    )

    assert exit_code == 0
    assert "ran" not in called
    assert "nothing to compute" in capsys.readouterr().out


def test_main_no_gui_runs_engine_and_never_imports_gui(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(batch_cli, "load_settings", lambda config: settings)

    bond = Bond(issuer="FRA", maturity=date(2029, 1, 1), bond_id="B1")
    trade_date = date(2020, 1, 2)
    plan = BatchPlan(
        issuer_plans=(
            IssuerPlan(
                issuer="FRA",
                trade_dates=(trade_date,),
                bonds=(bond,),
                work_items=(WorkItem(issuer="FRA", trade_date=trade_date, bonds=(bond,)),),
            ),
        )
    )
    monkeypatch.setattr(batch_cli, "build_plan", lambda *a, **k: plan)

    called = {}

    def fake_run(self, run_plan, run_settings, **kwargs):
        called["ran"] = True
        called["plan"] = run_plan
        called["workers"] = kwargs["workers"]
        called["curve_label"] = kwargs["curve_label"]

    monkeypatch.setattr(batch_cli.BatchEngine, "run", fake_run)
    monkeypatch.setattr(
        batch_cli, "run_gui_standalone", lambda *a, **k: pytest.fail("GUI must not launch")
    )

    exit_code = batch_cli.main(
        [
            "--issuer", "fra", "--start", "2020-01-01", "--end", "2020-01-31",
            "--no-gui", "--workers", "2",
        ]
    )

    assert exit_code == 0
    assert called["ran"] is True
    assert called["plan"] is plan
    assert called["workers"] == 2
    assert called["curve_label"] == "BOND_ZERO"


def test_main_future_no_gui_short_circuits_on_empty_plan(monkeypatch, tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(batch_cli, "load_settings", lambda config: settings)

    empty_plan = FutureBatchPlan(series_plans=())
    monkeypatch.setattr(batch_cli, "build_future_plan", lambda *a, **k: empty_plan)

    called = {}
    monkeypatch.setattr(
        batch_cli.FutureBatchEngine, "run", lambda self, *a, **k: called.setdefault("ran", True)
    )

    exit_code = batch_cli.main(
        [
            "--future", "fgbm", "--delivery", "HMUZ",
            "--start", "2020-01-01", "--end", "2020-01-31", "--no-gui",
        ]
    )

    assert exit_code == 0
    assert "ran" not in called
    assert "nothing to compute" in capsys.readouterr().out


def test_main_future_no_gui_runs_engine_and_never_imports_gui(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(batch_cli, "load_settings", lambda config: settings)

    future = BondFuture(BOND_FUTURE_CONVENTIONS["FBTP"], 9, 2020)
    bond = Bond(issuer="ITA", maturity=date(2030, 9, 10), bond_id="IT0001")
    basket = DeliveryBasket(bond_future=future, members=(BasketMember(bond),))
    trade_date = date(2020, 6, 15)
    plan = FutureBatchPlan(
        series_plans=(
            FutureSeriesPlan(
                future_code="FBTP",
                contracts=(future,),
                trade_dates=(trade_date,),
                work_items=(FutureWorkItem(issuer="ITA", trade_date=trade_date, baskets=(basket,)),),
            ),
        )
    )
    monkeypatch.setattr(batch_cli, "build_future_plan", lambda *a, **k: plan)

    called = {}

    def fake_run(self, run_plan, run_settings, **kwargs):
        called["ran"] = True
        called["plan"] = run_plan
        called["workers"] = kwargs["workers"]
        called["curve_label"] = kwargs["curve_label"]

    monkeypatch.setattr(batch_cli.FutureBatchEngine, "run", fake_run)
    monkeypatch.setattr(
        batch_cli, "run_future_gui_standalone", lambda *a, **k: pytest.fail("GUI must not launch")
    )

    exit_code = batch_cli.main(
        [
            "--future", "fbtp", "--delivery", "U",
            "--start", "2020-01-01", "--end", "2020-12-31",
            "--no-gui", "--workers", "3",
        ]
    )

    assert exit_code == 0
    assert called["ran"] is True
    assert called["plan"] is plan
    assert called["workers"] == 3
    assert called["curve_label"] == "BOND_ZERO"
