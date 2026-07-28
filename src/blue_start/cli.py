from __future__ import annotations

import argparse
import json
import sys

from .pipeline import (
    analyze_following,
    analyze_nodes,
    analyze_starterpacks,
    doctor,
    prepare_database,
)
from .hypergraph import (
    compute_hypergraph_kcore,
    compute_pair_cooccurrence,
    compute_s_line_counts,
    compute_starterpack_components,
)
from .ranking import compute_kendall_tau
from .reference import import_upstream_reference
from .plots import plot_all, plot_following, plot_kendall, plot_mesoscale, plot_nodes, plot_starterpacks
from .validation import inventory, validate_datasets


def _human_size(value: int | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def _inventory_command() -> int:
    print(f"{'DATASET':28} {'SIZE':>12}  PATH")
    for row in inventory():
        print(f"{row['dataset']:28} {_human_size(row['bytes']):>12}  {row['path'] or 'MISSING'}")
    return 0


def _validate_command(sample_size: int) -> int:
    results = validate_datasets(sample_size)
    for result in results:
        marker = "OK" if result.ok else "FAIL"
        print(f"[{marker:4}] {result.dataset}: {result.detail}")
    return 0 if all(result.ok for result in results) else 1


def _print_run_result(result: object) -> int:
    print(json.dumps(result.summary, indent=2, ensure_ascii=False))
    print(f"Completed in {result.seconds:.2f} seconds")
    for path in result.outputs:
        print(f"  -> {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Blue Start dataset utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inventory", help="show resolved dataset paths and sizes")
    validate = subparsers.add_parser("validate", help="run lightweight data checks")
    validate.add_argument("--sample-size", type=int, default=100)
    subparsers.add_parser("doctor", help="check DuckDB, paths, and resource settings")
    prepare = subparsers.add_parser(
        "prepare",
        help="create the DuckDB database, local tables, and external follow view",
    )
    prepare.add_argument(
        "--rebuild",
        action="store_true",
        help="recreate materialized node and starter-pack tables",
    )
    subparsers.add_parser("nodes", help="compute node statistics")
    following = subparsers.add_parser(
        "following",
        help="compute following-network degree and temporal statistics",
    )
    following.add_argument(
        "--row-limit",
        type=int,
        help="analyze only the first N follow edges (recommended for a smoke test)",
    )
    following.add_argument(
        "--time-std",
        action="store_true",
        help="also compute per-node standard deviation of follow dates",
    )
    following.add_argument(
        "--impossible-timestamps",
        action="store_true",
        help="also join nodes and count physically impossible timestamps",
    )
    following.add_argument(
        "--force",
        action="store_true",
        help="recompute basic degree/volume tables even when cached",
    )
    subparsers.add_parser("starterpacks", help="compute starter-pack statistics")
    subparsers.add_parser(
        "starterpack-components",
        help="compute exact hypergraph connected components with union-find",
    )
    pair = subparsers.add_parser(
        "pair-cooccurrence",
        help="compute exact user-pair co-occurrences for bounded pack sizes",
    )
    pair.add_argument("--max-pack-size", type=int, default=50)
    s_line = subparsers.add_parser(
        "s-line",
        help="compute s-line counts after filtering hyper-hub members",
    )
    s_line.add_argument("--s-max", type=int, default=5)
    s_line.add_argument("--max-member-degree", type=int, default=5000)
    subparsers.add_parser(
        "starterpack-kcore",
        help="run the exact, multi-gigabyte hypergraph k-core calculation",
    )
    kendall = subparsers.add_parser(
        "kendall-tau",
        help="compare follow and starter-pack degree rankings",
    )
    kendall.add_argument("--follow-profile", default="full")
    kendall.add_argument("--top-k", type=int, default=1_000_000)
    subparsers.add_parser(
        "reference-import",
        help="import compact official results for HPC-only analyses",
    )
    plots = subparsers.add_parser("plot", help="render reproduction figures")
    plots.add_argument(
        "target",
        choices=("all", "nodes", "following", "starterpacks", "mesoscale", "kendall"),
    )
    plots.add_argument("--follow-profile", default="full")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inventory":
        return _inventory_command()
    if args.command == "validate":
        return _validate_command(args.sample_size)
    if args.command == "doctor":
        print(json.dumps(doctor(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "prepare":
        return _print_run_result(prepare_database(rebuild=args.rebuild))
    if args.command == "nodes":
        return _print_run_result(analyze_nodes())
    if args.command == "following":
        return _print_run_result(
            analyze_following(
                row_limit=args.row_limit,
                include_time_std=args.time_std,
                include_impossible_timestamps=args.impossible_timestamps,
                force=args.force,
            )
        )
    if args.command == "starterpacks":
        return _print_run_result(analyze_starterpacks())
    if args.command == "starterpack-components":
        return _print_run_result(compute_starterpack_components())
    if args.command == "pair-cooccurrence":
        return _print_run_result(
            compute_pair_cooccurrence(max_pack_size=args.max_pack_size)
        )
    if args.command == "s-line":
        return _print_run_result(
            compute_s_line_counts(
                s_max=args.s_max,
                max_member_degree=args.max_member_degree,
            )
        )
    if args.command == "starterpack-kcore":
        return _print_run_result(compute_hypergraph_kcore())
    if args.command == "kendall-tau":
        return _print_run_result(
            compute_kendall_tau(
                follow_profile=args.follow_profile,
                top_k=args.top_k,
            )
        )
    if args.command == "reference-import":
        return _print_run_result(import_upstream_reference())
    if args.command == "plot":
        functions = {
            "nodes": lambda: plot_nodes(),
            "following": lambda: plot_following(args.follow_profile),
            "starterpacks": lambda: plot_starterpacks(),
            "mesoscale": lambda: plot_mesoscale(),
            "kendall": lambda: plot_kendall(args.follow_profile),
            "all": lambda: plot_all(args.follow_profile),
        }
        outputs = functions[args.target]()
        for path in outputs:
            print(f"  -> {path}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
