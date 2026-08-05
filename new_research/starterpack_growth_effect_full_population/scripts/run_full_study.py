from __future__ import annotations

import json
from pathlib import Path

from new_research.starterpack_growth_effect_full_population.code.analysis import (
    StarterPackGrowthConfig,
    run_starterpack_growth_study,
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    summary = root / "outputs" / "summaries" / "starterpack_growth_effect_full_population.json"
    if summary.exists():
        print(f"[OK] Completed result already exists: {summary}")
        return 0
    try:
        result = run_starterpack_growth_study(StarterPackGrowthConfig())
    except Exception as error:
        # The full 2.4B-edge reciprocity join can reach the intentionally low
        # workstation memory cap after all core outputs have been committed.
        # Recover that final stage exactly in resumable hash partitions.
        if "Out of Memory" not in str(error):
            raise
        print("[RECOVER] Monolithic quality join reached its safe memory cap.")
        from recover_network_quality import main as recover_network_quality
        from finalize_full_study import main as finalize_full_study

        recovery_status = recover_network_quality()
        if recovery_status != 0:
            return recovery_status
        return finalize_full_study()
    print(
        json.dumps(
            {
                "task": result.task,
                "seconds": result.seconds,
                "outputs": result.outputs,
                "population_counts": result.summary["population_counts"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
