from __future__ import annotations

import json
from pathlib import Path

from .duckdb_backend import connect
from .reference import upstream_root
from .settings import load_settings


COLORS = ("#2d5d83", "#4f9bd9", "#077187", "#3dfaff", "#8b888e")


def _libraries():
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Plotting needs Matplotlib and NumPy. Install: "
            'python -m pip install -e ".[analysis]"'
        ) from exc
    return plt, np


def _save(figure, stem: Path) -> list[str]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    png = stem.with_suffix(".png")
    pdf = stem.with_suffix(".pdf")
    figure.savefig(png, dpi=180, bbox_inches="tight")
    figure.savefig(pdf, dpi=300, bbox_inches="tight")
    return [str(png), str(pdf)]


def plot_nodes() -> list[str]:
    plt, _ = _libraries()
    settings = load_settings()
    with connect(settings, read_only=True) as con:
        creation = con.execute(
            """
            SELECT date_created, account_count
            FROM results.node_creation_volume
            ORDER BY date_created
            """
        ).fetchall()
        statuses = con.execute(
            """
            SELECT
                CASE
                    WHEN active THEN 'Active'
                    WHEN active = false THEN 'Inactive'
                    ELSE 'Unknown'
                END AS label,
                sum(account_count)
            FROM results.node_status_counts
            GROUP BY label
            ORDER BY sum(account_count) DESC
            """
        ).fetchall()

    figure, axes = plt.subplots(2, 1, figsize=(11, 8))
    axes[0].scatter(
        [row[0] for row in creation],
        [row[1] for row in creation],
        s=10,
        color=COLORS[0],
    )
    axes[0].set_yscale("log")
    axes[0].set_xlabel("Date")
    axes[0].set_ylabel("Accounts created")
    axes[0].set_title("(a) Account creation volume")

    axes[1].bar(
        [row[0] for row in statuses],
        [row[1] for row in statuses],
        color=COLORS[: len(statuses)],
    )
    axes[1].set_ylabel("Count")
    axes[1].set_title("(b) Account activity status")
    figure.tight_layout()
    return _save(figure, settings.figure_outputs / "node_creation_and_activity")


def plot_following(profile: str = "full") -> list[str]:
    plt, np = _libraries()
    if not profile.replace("_", "").isalnum():
        raise ValueError("invalid profile")
    settings = load_settings()
    with connect(settings, read_only=True) as con:
        volume = con.execute(
            f"""
            SELECT date_followed, follow_count
            FROM results.follow_volume_{profile}
            WHERE date_followed >= DATE '2023-01-01'
            ORDER BY date_followed
            """
        ).fetchall()
        degrees = con.execute(
            f"""
            SELECT degree, in_node_count, out_node_count
            FROM results.follow_degree_distribution_{profile}
            ORDER BY degree
            """
        ).fetchall()
        scc = con.execute(
            """
            SELECT component_size, component_count
            FROM results.reference_follow_scc_distribution
            ORDER BY component_size
            """
        ).fetchall()
        wcc = con.execute(
            """
            SELECT component_size, component_count
            FROM results.reference_follow_wcc_distribution
            ORDER BY component_size
            """
        ).fetchall()

    figure = plt.figure(figsize=(11, 8))
    grid = figure.add_gridspec(2, 2)
    top = figure.add_subplot(grid[0, :])
    top.scatter(
        [row[0] for row in volume if row[0] is not None],
        [row[1] for row in volume if row[0] is not None],
        s=10,
        color=COLORS[0],
    )
    top.set_yscale("log")
    top.set_xlabel("Date")
    top.set_ylabel("Number of follows")
    top.set_title("(a) Follow volume")

    degree_axis = figure.add_subplot(grid[1, 0])
    degree = np.array([row[0] for row in degrees], dtype=float)
    incoming = np.array([row[1] for row in degrees], dtype=float)
    outgoing = np.array([row[2] for row in degrees], dtype=float)
    degree_axis.scatter(degree[incoming > 0], incoming[incoming > 0], s=8, label="in-degree")
    degree_axis.scatter(
        degree[outgoing > 0],
        outgoing[outgoing > 0],
        s=8,
        marker="^",
        label="out-degree",
    )
    degree_axis.set_xscale("log")
    degree_axis.set_yscale("log")
    degree_axis.set_xlabel("Degree")
    degree_axis.set_ylabel("Node count")
    degree_axis.set_title("(b) Degree distribution")
    degree_axis.legend()

    component_axis = figure.add_subplot(grid[1, 1])
    component_axis.scatter(
        [row[0] for row in scc],
        [row[1] for row in scc],
        s=10,
        label="Strongly connected",
    )
    component_axis.scatter(
        [row[0] for row in wcc],
        [row[1] for row in wcc],
        s=10,
        marker="^",
        label="Weakly connected",
    )
    component_axis.set_xscale("log")
    component_axis.set_yscale("log")
    component_axis.set_xlabel("Component size")
    component_axis.set_ylabel("Number")
    component_axis.set_title("(c) Official component results")
    component_axis.legend()
    figure.tight_layout()
    return _save(figure, settings.figure_outputs / f"following_network_{profile}")


def plot_starterpacks() -> list[str]:
    plt, np = _libraries()
    settings = load_settings()
    with connect(settings, read_only=True) as con:
        degrees = con.execute(
            """
            SELECT degree, node_count
            FROM results.starterpack_degree_distribution
            ORDER BY degree
            """
        ).fetchall()
        sizes = con.execute(
            """
            SELECT pack_size, pack_count
            FROM results.starterpack_size_distribution
            ORDER BY pack_size
            """
        ).fetchall()
        components = con.execute(
            """
            SELECT component_size, component_count
            FROM results.starterpack_component_sizes
            ORDER BY component_size
            """
        ).fetchall()
        creation = con.execute(
            """
            SELECT date_created, pack_count
            FROM results.starterpack_creation_volume
            WHERE date_created >= DATE '2020-01-01'
            ORDER BY date_created
            """
        ).fetchall()
        ages = con.execute(
            """
            SELECT account_age_days
            FROM results.starterpack_creator_ages
            WHERE account_age_days IS NOT NULL
            """
        ).fetchall()

    outputs: list[str] = []
    figure, axes = plt.subplots(1, 3, figsize=(13, 4))
    axes[0].scatter([r[0] for r in degrees], [r[1] for r in degrees], s=8)
    axes[0].set_xscale("log")
    axes[0].set_yscale("log")
    axes[0].set_xlabel("Starter-pack degree")
    axes[0].set_ylabel("Node count")
    axes[0].set_title("(a) Membership degree")

    positive_sizes = [row for row in sizes if row[0] > 0]
    axes[1].scatter(
        [r[0] for r in positive_sizes],
        [r[1] for r in positive_sizes],
        s=8,
    )
    axes[1].set_xscale("log")
    axes[1].set_yscale("log")
    axes[1].set_xlabel("Pack size")
    axes[1].set_ylabel("Pack count")
    axes[1].set_title("(b) Hyperedge size")

    axes[2].scatter([r[0] for r in components], [r[1] for r in components], s=10)
    axes[2].set_xscale("log")
    axes[2].set_yscale("log")
    axes[2].set_xlabel("Component size")
    axes[2].set_ylabel("Number")
    axes[2].set_title("(c) Components")
    figure.tight_layout()
    outputs.extend(_save(figure, settings.figure_outputs / "starterpack_basic"))

    temporal, temporal_axes = plt.subplots(1, 2, figsize=(11, 4))
    temporal_axes[0].scatter(
        [r[0] for r in creation if r[0] is not None],
        [r[1] for r in creation if r[0] is not None],
        s=8,
    )
    temporal_axes[0].set_yscale("log")
    temporal_axes[0].set_xlabel("Date")
    temporal_axes[0].set_ylabel("Starter packs created")
    age_values = np.array([row[0] for row in ages], dtype=float)
    temporal_axes[1].hist(age_values, bins=100, log=True, color=COLORS[0])
    temporal_axes[1].set_xlabel("Account age at pack creation (days)")
    temporal_axes[1].set_ylabel("Count")
    temporal.tight_layout()
    outputs.extend(_save(temporal, settings.figure_outputs / "starterpack_temporal"))
    return outputs


def plot_mesoscale() -> list[str]:
    plt, np = _libraries()
    settings = load_settings()
    with connect(settings, read_only=True) as con:
        s_line = con.execute(
            "SELECT s, edges FROM results.reference_s_line_counts ORDER BY s"
        ).fetchall()
        kcore = con.execute(
            """
            SELECT core_number, node_count
            FROM results.reference_kcore_distribution
            ORDER BY core_number
            """
        ).fetchall()
        pairs = con.execute(
            """
            SELECT cooccurrence, pair_count
            FROM results.reference_pair_cooccurrence_distribution
            ORDER BY cooccurrence
            """
        ).fetchall()
        pack_count = con.execute("SELECT count(*) FROM starterpacks").fetchone()[0]

    entropy_path = upstream_root() / "data" / "edge_entropy.json"
    entropies: list[float] = []
    if entropy_path.exists():
        payload = json.loads(entropy_path.read_text(encoding="utf-8"))
        values = payload.values() if isinstance(payload, dict) else payload
        entropies = [float(value) for value in values]

    figure, axes = plt.subplots(2, 2, figsize=(11, 8))
    max_pairs = pack_count * (pack_count - 1) / 2
    axes[0, 0].scatter(
        [row[0] for row in s_line],
        [row[1] / max_pairs for row in s_line],
        s=10,
    )
    axes[0, 0].set_xscale("log")
    axes[0, 0].set_yscale("log")
    axes[0, 0].set_xlabel("Edge overlap s")
    axes[0, 0].set_ylabel("Line-graph density")

    axes[0, 1].scatter([r[0] for r in kcore], [r[1] for r in kcore], s=8)
    axes[0, 1].set_xscale("log")
    axes[0, 1].set_yscale("log")
    axes[0, 1].set_xlabel("Coreness")
    axes[0, 1].set_ylabel("Node count")

    axes[1, 0].scatter([r[0] for r in pairs], [r[1] for r in pairs], s=8)
    axes[1, 0].set_xscale("log")
    axes[1, 0].set_yscale("log")
    axes[1, 0].set_xlabel("Pair co-occurrence")
    axes[1, 0].set_ylabel("Pair count")

    if entropies:
        axes[1, 1].hist(entropies, bins=100, color=COLORS[0])
    axes[1, 1].set_xlabel("Normalized edge entropy")
    axes[1, 1].set_ylabel("Count")
    figure.tight_layout()
    return _save(figure, settings.figure_outputs / "starterpack_mesoscale")


def plot_kendall(profile: str = "full") -> list[str]:
    plt, _ = _libraries()
    settings = load_settings()
    path = settings.summary_outputs / f"kendall_tau_{profile}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    figure, axis = plt.subplots(figsize=(11, 4))
    axis.scatter(
        payload["x_follow_to_starter"],
        payload["tau_follow_to_starter"],
        s=18,
        label="follow -> starter pack",
    )
    axis.scatter(
        payload["x_starter_to_follow"],
        payload["tau_starter_to_follow"],
        s=18,
        marker="^",
        label="starter pack -> follow",
    )
    axis.set_xscale("log")
    axis.set_xlabel("Number of top-ranked elements")
    axis.set_ylabel("Kendall tau-b")
    axis.legend()
    figure.tight_layout()
    return _save(figure, settings.figure_outputs / f"kendall_tau_{profile}")


def plot_all(profile: str = "full") -> list[str]:
    outputs: list[str] = []
    outputs.extend(plot_nodes())
    outputs.extend(plot_following(profile))
    outputs.extend(plot_starterpacks())
    outputs.extend(plot_mesoscale())
    outputs.extend(plot_kendall(profile))
    return outputs
