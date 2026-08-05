from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .pipeline import (
    analyze_following,
    analyze_nodes,
    analyze_starterpacks,
    doctor,
    prepare_database,
)
from .hypergraph import (
    compute_pair_cooccurrence,
    compute_s_line_counts,
    compute_starterpack_components,
)
from .ranking import compute_kendall_tau
from .reference import import_upstream_reference
from .plots import (
    plot_all,
    plot_following,
    plot_kendall,
    plot_leiden,
    plot_mesoscale,
    plot_nodes,
    plot_projection,
    plot_starterpacks,
)
from .advanced import (
    build_weighted_clique_projection,
    compute_configuration_model,
    compute_edge_entropy,
    compute_follow_wcc,
    compute_hypergraph_kcore_compact,
    compute_pair_cooccurrence_paper,
)
from .scc import compute_follow_scc_exact
from .sline import compute_sline_full
from .leiden import (
    build_leiden_input,
    compute_leiden_full,
    import_native_leiden_result,
)
from .validation import inventory, validate_datasets
from new_research.starterpack_growth_effect.code.analysis import (
    StarterPackGrowthConfig,
    run_starterpack_growth_study,
)


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
    paper_pair = subparsers.add_parser(
        "pair-cooccurrence-paper",
        help="run the paper-compatible exact/sampled pair co-occurrence analysis",
    )
    paper_pair.add_argument("--maximum-exact-pack-size", type=int, default=4_069)
    paper_pair.add_argument("--sample-size", type=int, default=1_000)
    paper_pair.add_argument("--partitions", type=int, default=256)
    paper_pair.add_argument("--seed", type=int, default=0)
    paper_pair.add_argument("--rebuild-pairs", action="store_true")
    paper_pair.add_argument("--keep-pair-rows", action="store_true")
    projection = subparsers.add_parser(
        "clique-projection",
        help="build the full disk-backed weighted Starter Pack projection",
    )
    projection.add_argument("--maximum-exact-pack-size", type=int, default=4_069)
    projection.add_argument("--sample-size", type=int, default=1_000)
    projection.add_argument("--partitions", type=int, default=256)
    projection.add_argument("--seed", type=int, default=0)
    projection.add_argument("--rebuild-projection", action="store_true")
    s_line = subparsers.add_parser(
        "s-line",
        help="compute s-line counts after filtering hyper-hub members",
    )
    s_line.add_argument("--s-max", type=int, default=5)
    s_line.add_argument("--max-member-degree", type=int, default=5000)
    s_line_full = subparsers.add_parser(
        "s-line-full",
        help="compute exact unrestricted s-line counts in checkpointed native batches",
    )
    s_line_full.add_argument("--s-max", type=int, default=345)
    s_line_full.add_argument("--batch-packs", type=int, default=4096)
    s_line_full.add_argument("--threads", type=int)
    s_line_full.add_argument("--maximum-new-batches", type=int)
    s_line_full.add_argument("--rebuild", action="store_true")
    subparsers.add_parser(
        "starterpack-kcore",
        help="run the optimized exact hypergraph k-core calculation",
    )
    wcc = subparsers.add_parser(
        "follow-wcc",
        help="compute exact weakly connected components of the full follow graph",
    )
    wcc.add_argument("--batch-size", type=int, default=4_000_000)
    scc = subparsers.add_parser(
        "follow-scc",
        help="compute exact SCCs with checkpointed disk-backed CSR arrays",
    )
    scc.add_argument(
        "--maximum-new-buckets",
        type=int,
        help="build only this many new CSR buckets, then stop safely",
    )
    scc.add_argument("--rebuild", action="store_true")
    entropy = subparsers.add_parser(
        "edge-entropy",
        help="recompute Starter Pack entropy from selected Leiden labels",
    )
    entropy.add_argument(
        "--label-source",
        choices=("official", "independent"),
        default="official",
    )
    configuration = subparsers.add_parser(
        "configuration-model",
        help="run the paper-compatible randomized hypergraph entropy analysis",
    )
    configuration.add_argument("--swaps-per-edge", type=int, default=10)
    configuration.add_argument("--seed", type=int, default=0)
    configuration.add_argument(
        "--label-source",
        choices=("official", "independent"),
        default="official",
    )
    leiden = subparsers.add_parser(
        "starterpack-leiden",
        help="run the full independent Leiden clustering",
    )
    leiden.add_argument(
        "--input-only",
        action="store_true",
        help="build and validate the giant unweighted edge array without Leiden",
    )
    leiden.add_argument(
        "--import-native",
        action="store_true",
        help="import native membership or the validated portable result",
    )
    leiden.add_argument(
        "--python-backend",
        action="store_true",
        help="explicitly allow the high-memory 64-bit Python backend",
    )
    leiden.add_argument("--rebuild", action="store_true")
    kendall = subparsers.add_parser(
        "kendall-tau",
        help="compare follow and starter-pack degree rankings",
    )
    kendall.add_argument("--follow-profile", default="full")
    kendall.add_argument("--top-k", type=int, default=1_000_000)
    growth = subparsers.add_parser(
        "starterpack-growth-study",
        help="estimate user growth after first Starter Pack inclusion with matched controls",
    )
    growth.add_argument("--treatment-rows", type=int, default=100_000)
    growth.add_argument("--controls-per-treatment", type=int, default=8)
    growth.add_argument("--min-account-age-days", type=int, default=30)
    growth.add_argument(
        "--min-treatment-date", type=date.fromisoformat, default=date(2024, 6, 1)
    )
    growth.add_argument("--horizon-days", type=int, default=90)
    growth.add_argument("--max-final-in-degree", type=int, default=100_000)
    growth.add_argument("--max-final-out-degree", type=int, default=100_000)
    growth.add_argument("--propensity-caliper", type=float, default=0.25)
    growth.add_argument("--max-control-reuse", type=int, default=10)
    growth.add_argument("--seed", type=int, default=15)
    subparsers.add_parser(
        "reference-import",
        help="import compact official results for independent validation",
    )
    plots = subparsers.add_parser("plot", help="render reproduction figures")
    plots.add_argument(
        "target",
        choices=(
            "all",
            "nodes",
            "following",
            "starterpacks",
            "mesoscale",
            "projection",
            "leiden",
            "kendall",
        ),
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
    if args.command == "pair-cooccurrence-paper":
        return _print_run_result(
            compute_pair_cooccurrence_paper(
                maximum_exact_pack_size=args.maximum_exact_pack_size,
                sample_size=args.sample_size,
                partitions=args.partitions,
                seed=args.seed,
                rebuild_pairs=args.rebuild_pairs,
                keep_pair_rows=args.keep_pair_rows,
            )
        )
    if args.command == "clique-projection":
        return _print_run_result(
            build_weighted_clique_projection(
                maximum_exact_pack_size=args.maximum_exact_pack_size,
                sample_size=args.sample_size,
                partitions=args.partitions,
                seed=args.seed,
                rebuild_projection=args.rebuild_projection,
            )
        )
    if args.command == "s-line":
        return _print_run_result(
            compute_s_line_counts(
                s_max=args.s_max,
                max_member_degree=args.max_member_degree,
            )
        )
    if args.command == "s-line-full":
        return _print_run_result(
            compute_sline_full(
                s_max=args.s_max,
                batch_packs=args.batch_packs,
                threads=args.threads,
                maximum_new_batches=args.maximum_new_batches,
                rebuild=args.rebuild,
            )
        )
    if args.command == "starterpack-kcore":
        return _print_run_result(compute_hypergraph_kcore_compact())
    if args.command == "follow-wcc":
        return _print_run_result(compute_follow_wcc(batch_size=args.batch_size))
    if args.command == "follow-scc":
        return _print_run_result(
            compute_follow_scc_exact(
                maximum_new_buckets=args.maximum_new_buckets,
                rebuild=args.rebuild,
            )
        )
    if args.command == "edge-entropy":
        return _print_run_result(
            compute_edge_entropy(label_source=args.label_source)
        )
    if args.command == "configuration-model":
        return _print_run_result(
            compute_configuration_model(
                swaps_per_edge=args.swaps_per_edge,
                seed=args.seed,
                label_source=args.label_source,
            )
        )
    if args.command == "starterpack-leiden":
        if args.import_native:
            return _print_run_result(import_native_leiden_result())
        if args.input_only:
            return _print_run_result(build_leiden_input(rebuild=args.rebuild))
        if args.python_backend:
            return _print_run_result(compute_leiden_full(rebuild=args.rebuild))
        return _print_run_result(import_native_leiden_result())
    if args.command == "kendall-tau":
        return _print_run_result(
            compute_kendall_tau(
                follow_profile=args.follow_profile,
                top_k=args.top_k,
            )
        )
    if args.command == "starterpack-growth-study":
        return _print_run_result(
            run_starterpack_growth_study(
                StarterPackGrowthConfig(
                    treatment_rows=args.treatment_rows,
                    controls_per_treatment=args.controls_per_treatment,
                    min_account_age_days=args.min_account_age_days,
                    min_treatment_date=args.min_treatment_date,
                    horizon_days=args.horizon_days,
                    max_final_in_degree=args.max_final_in_degree,
                    max_final_out_degree=args.max_final_out_degree,
                    propensity_caliper=args.propensity_caliper,
                    max_control_reuse=args.max_control_reuse,
                    seed=args.seed,
                )
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
            "projection": lambda: plot_projection(),
            "leiden": lambda: plot_leiden(),
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
