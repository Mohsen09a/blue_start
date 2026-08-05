import unittest

import numpy as np

from new_research.starterpack_growth_effect.code.analysis import (
    age_band,
    degree_band,
    fit_propensity_model,
    nearest_propensity_matches,
    pack_size_band,
    paired_effect,
    standardized_mean_difference,
)


class StarterPackGrowthTests(unittest.TestCase):
    def test_bins_have_stable_boundaries(self) -> None:
        self.assertEqual(age_band(90), "30-90 days")
        self.assertEqual(age_band(91), "91-365 days")
        self.assertEqual(degree_band(99), "10-99")
        self.assertEqual(degree_band(100), "100-999")
        self.assertEqual(pack_size_band(50), "small (<=50)")
        self.assertEqual(pack_size_band(51), "medium (51-150)")

    def test_standardized_mean_difference_reaches_zero(self) -> None:
        values = np.asarray([1.0, 2.0, 3.0])
        self.assertEqual(standardized_mean_difference(values, values), 0.0)

    def test_propensity_model_orders_exposure_risk(self) -> None:
        rng = np.random.default_rng(15)
        feature = rng.normal(size=4_000)
        treatment_probability = 1.0 / (1.0 + np.exp(-feature))
        treated = rng.random(len(feature)) < treatment_probability
        scores, diagnostics = fit_propensity_model(feature[:, None], treated)
        self.assertTrue(diagnostics["converged"])
        self.assertGreater(float(scores[feature > 1].mean()), float(scores[feature < -1].mean()))

    def test_matching_respects_exact_strata(self) -> None:
        scores = np.asarray([0.2, 0.8, 0.21, 0.79])
        treated = np.asarray([True, True, False, False])
        strata = np.asarray([1, 2, 1, 2])
        treated_indexes, control_indexes, _, diagnostics = nearest_propensity_matches(
            scores, treated, strata, caliper_standard_deviations=1.0, maximum_reuse=1
        )
        self.assertEqual(len(treated_indexes), 2)
        self.assertTrue(np.all(strata[treated_indexes] == strata[control_indexes]))
        self.assertEqual(diagnostics["matched_treated"], 2)

    def test_paired_effect_uses_within_pair_difference(self) -> None:
        result = paired_effect(np.asarray([3.0, 5.0]), np.asarray([1.0, 2.0]))
        self.assertAlmostEqual(result["mean_difference"], 2.5)
        self.assertEqual(result["pairs"], 2)

    def test_cluster_robust_effect_is_reported(self) -> None:
        result = paired_effect(
            np.asarray([3.0, 5.0, 7.0]),
            np.asarray([1.0, 1.0, 2.0]),
            np.asarray([10, 10, 11]),
        )
        self.assertEqual(result["standard_error_method"], "control-cluster-robust")


if __name__ == "__main__":
    unittest.main()
