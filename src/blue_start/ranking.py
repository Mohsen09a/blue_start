from __future__ import annotations

import json
import time

from .duckdb_backend import connect
from .pipeline import RunResult
from .settings import load_settings


def _log_bin_stats(x, y, *, bins: int = 30):
    import numpy as np

    edges = np.logspace(np.log10(x.min()), np.log10(x.max()), bins + 1)
    centers = np.sqrt(edges[:-1] * edges[1:])
    values = np.full(bins, np.nan)
    for index, (lower, upper) in enumerate(zip(edges[:-1], edges[1:])):
        mask = (x >= lower) & (x < upper)
        if mask.any():
            values[index] = np.mean(y[mask])
    valid = ~np.isnan(values)
    return centers[valid], values[valid]


def compute_kendall_tau(
    *,
    follow_profile: str = "full",
    top_k: int = 1_000_000,
) -> RunResult:
    """Reproduce the upstream Kendall-tau comparison from materialized degrees."""
    try:
        import numpy as np
        from scipy.stats import kendalltau
    except ImportError as exc:
        raise RuntimeError(
            "This command needs NumPy and SciPy. Install: "
            'python -m pip install -e ".[analysis]"'
        ) from exc

    if top_k < 10:
        raise ValueError("top_k must be at least 10")
    if not follow_profile.replace("_", "").isalnum():
        raise ValueError("invalid follow profile")

    settings = load_settings()
    started = time.perf_counter()
    follow_table = f"results.follow_degrees_{follow_profile}"

    with connect(settings) as con:
        exists = con.execute(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = 'results' AND table_name = ?
            """,
            [f"follow_degrees_{follow_profile}"],
        ).fetchone()[0]
        if not exists:
            raise RuntimeError(
                f"{follow_table} does not exist. Run the following analysis first."
            )

        follow_rows = con.execute(
            f"""
            SELECT node_id, in_degree + out_degree AS degree
            FROM {follow_table}
            ORDER BY degree DESC, node_id
            LIMIT {int(top_k)}
            """
        ).fetchall()
        starter_rows = con.execute(
            f"""
            SELECT member_id, count(*)::UBIGINT AS degree
            FROM starterpack_memberships
            GROUP BY member_id
            ORDER BY degree DESC, member_id
            LIMIT {int(top_k)}
            """
        ).fetchall()

    follow = {int(node): int(degree) for node, degree in follow_rows}
    starter = {int(node): int(degree) for node, degree in starter_rows}
    common = sorted(follow.keys() & starter.keys())
    if len(common) < 10:
        raise RuntimeError("Too few common nodes for a rank comparison.")

    follow_degree = np.array([follow[node] for node in common])
    starter_degree = np.array([starter[node] for node in common])
    sizes = np.unique(
        np.logspace(0, np.log10(len(common) - 1), 500, dtype=int) + 1
    )
    sizes = sizes[sizes >= 2]

    follow_order = np.argsort(follow_degree)
    starter_order = np.argsort(starter_degree)
    tau_follow_to_starter = np.array(
        [
            kendalltau(
                follow_degree[follow_order][-size:],
                starter_degree[follow_order][-size:],
                variant="b",
            ).statistic
            for size in sizes
        ]
    )
    tau_starter_to_follow = np.array(
        [
            kendalltau(
                starter_degree[starter_order][-size:],
                follow_degree[starter_order][-size:],
                variant="b",
            ).statistic
            for size in sizes
        ]
    )
    x1, y1 = _log_bin_stats(sizes, tau_follow_to_starter)
    x2, y2 = _log_bin_stats(sizes, tau_starter_to_follow)

    output = settings.summary_outputs / f"kendall_tau_{follow_profile}.json"
    payload = {
        "follow_profile": follow_profile,
        "requested_top_k": top_k,
        "common_top_nodes": len(common),
        "x_follow_to_starter": x1.tolist(),
        "tau_follow_to_starter": y1.tolist(),
        "x_starter_to_follow": x2.tolist(),
        "tau_starter_to_follow": y2.tolist(),
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return RunResult(
        task=f"kendall_tau_{follow_profile}",
        seconds=time.perf_counter() - started,
        outputs=[str(output)],
        summary={
            "follow_profile": follow_profile,
            "common_top_nodes": len(common),
        },
    )

