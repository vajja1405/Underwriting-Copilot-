"""A small fixture results folder for the resume-lines tests.

Every file holds only the keys the resume bullets read, with made-up test values (they describe no run). The values
are distinctive four-decimal numbers, so a test can check that every number in a bullet comes from these files.
"""

import json
import re
from pathlib import Path
from typing import Any

from uwcopilot.resume.text import STATIC_PHRASES

# A number token: thousands-separated (30,681) or plain (0.9412, 2015).
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# Small counts the script computes itself (for example how many business types beat a baseline).
SMALL_COUNTS = {str(value) for value in range(11)}


def ci(value: float, low: float, high: float, n: int | None = None) -> dict[str, Any]:
    """{value, ci_low, ci_high[, n]} as the docintel and agent results write it."""
    entry: dict[str, Any] = {"value": value, "ci_low": low, "ci_high": high}
    if n is not None:
        entry["n"] = n
    return entry


def risk_ci(estimate: float, low: float, high: float, n: int | None = None) -> dict[str, Any]:
    """{estimate, ci_low, ci_high[, n]} as the risk and Studio results write it."""
    entry: dict[str, Any] = {"estimate": estimate, "ci_low": low, "ci_high": high}
    if n is not None:
        entry["n"] = n
    return entry


def wilson(successes: int, n: int, rate: float, low: float, high: float) -> dict[str, Any]:
    """{successes, n, rate, wilson_ci95} as security.injection_eval.rate writes it."""
    return {"successes": successes, "n": n, "rate": rate, "wilson_ci95": [low, high]}


def lender_files() -> dict[str, Any]:
    """The Lender Desk results files."""
    return {
        "docintel_metrics.json": {
            "evaluation_set": {"n_packets": 12},
            "run": {"provider": None},
            "rules": {
                "cost": {"llm_calls": 0},
                "contradictions": {"recall": ci(0.8123, 0.7011, 0.9034), "precision": ci(0.6543, 0.5432, 0.7654)},
                "fields": {"overall": {"f1": ci(0.9123, 0.9012, 0.9234)}},
                "citations": {"supported_all_values": ci(0.9345, 0.9234, 0.9456)},
            },
        },
        "evidence_metrics.json": {
            "packets": {
                "results": {
                    "modes": {
                        "oracle": {
                            "next_best_evidence_agreement_circular": {
                                "label": "CIRCULAR: test fixture",
                                "top_3": {"value": 0.9911},
                            }
                        }
                    }
                }
            },
            "simulator": {
                "results": {
                    "headline": {
                        "comparison": "voi minus checklist, per case",
                        "n_cases": 77,
                        "voi_minus_checklist": {
                            "n_requests": {
                                "mean_first": 0.4321,
                                "mean_second": 1.2345,
                                "mean_difference": -0.8024,
                                "ci_low": -0.9135,
                                "ci_high": -0.6913,
                            },
                            "decision_error": {
                                "mean_first": 0.0312,
                                "mean_second": 0.0156,
                                "mean_difference": 0.0156,
                                "ci_low": 0.0,
                                "ci_high": 0.0312,
                            },
                        },
                    }
                }
            },
        },
        "agent_metrics.json": {
            "configuration": {"tracing": "spans created, not exported"},
            "deterministic_path": {
                "reached_human_checkpoint": ci(1.0, 1.0, 1.0, n=21),
                "decisions_recorded_without_a_human": 0,
            },
            "llm_dependent": {"cost_usd_per_application": "not measured: no LLM provider was used"},
        },
        "risk_metrics.json": {
            "credit": {
                "results": {
                    "split": {
                        "train": {"issue_years": [2010, 2011, 2012]},
                        "validation": {"issue_years": [2013]},
                        "test": {"issue_years": [2014, 2015]},
                    },
                    "models": {
                        "lightgbm": {
                            "test": {"auc": risk_ci(0.7234, 0.7123, 0.7345), "ks": risk_ci(0.3456, 0.3345, 0.3567)}
                        },
                        "logistic_scorecard": {"test": {"auc": risk_ci(0.7012, 0.6901, 0.7123)}},
                    },
                    "comparison": {"test_auc_difference": risk_ci(0.0222, 0.0111, 0.0333)},
                    "stability": {"score_psi_by_vintage": {"lightgbm": [{"issue_year": 2013, "psi": 0.0123}]}},
                    "explainability": {"reason_codes": {"lightgbm": ["fico_range_low"]}},
                    "scenario_sanity_checks": {"checks": [{"feature": "dti"}]},
                }
            },
            "insurance_frequency": {
                "results": {
                    "preferred_model": {"name": "lgbm"},
                    "test_metrics": {
                        "lgbm": {
                            "mean_poisson_deviance": risk_ci(0.4111, 0.4011, 0.4211),
                            "lorenz_gini": risk_ci(0.3111, 0.3011, 0.3211),
                        },
                        "glm": {
                            "mean_poisson_deviance": risk_ci(0.4222, 0.4122, 0.4322),
                            "lorenz_gini": risk_ci(0.2888, 0.2788, 0.2988),
                        },
                    },
                }
            },
            "insurance_severity": {
                "results": {
                    "expected_loss": {"served_pair": {"observed_to_predicted": risk_ci(1.0333, 0.9733, 1.0933)}}
                }
            },
        },
        "kyc_metrics.json": {
            "data": {"counts_by_label_and_split": {"true_match": {"test": 18}}},
            "selected_operating_point": {"method": "rules_only", "alert_budget_per_1000": 50},
            "operating_points": {
                "50": {
                    "rules_only": {
                        "test": {
                            "recall": {"value": 0.6111, "ci95_low": 0.5111, "ci95_high": 0.7111},
                            "alerts_per_1000": {"value": 48.7, "ci95_low": 41.2, "ci95_high": 55.3},
                            "clean_false_positive_rate": {"value": 0.0444, "ci95_low": 0.0333, "ci95_high": 0.0555},
                        }
                    }
                }
            },
        },
        "governance_metrics.json": {
            "release_gate": {
                "results": {
                    "verdict": "PASS",
                    "counts": {"rows": 9, "PASS": 7, "FAIL": 0, "MISSING": 0, "NOT_MEASURED": 2},
                }
            },
            "monitoring": {
                "results": {
                    "scenarios": {
                        "no_drift": {"claim_rate_multiplier": 1.0},
                        "claim_rate_up": {"claim_rate_multiplier": 1.3},
                    },
                    "validation": {
                        "no_drift": {"false_alert_rate": {"numerator": 2, "denominator": 40}},
                        "claim_rate_up": {"detection_rate": {"numerator": 37, "denominator": 40}},
                    },
                }
            },
        },
        "deployment_metrics.json": {"runs": {"local-container": {"target": {"kind": "local-container"}}}},
        "injection/injection_metrics_detector_only.json": {
            "status": "partial (detector only; the model layers were not run)",
            "layers": {name: "not measured (no model run for this layer)" for name in ("L0", "L1", "L2")},
            "detector": {"recall_normalized": {"all": wilson(9, 33, 0.2727, 0.1508, 0.4428)}},
            "action_level": {
                "status": "enforced by tests/security/test_containment.py; not an empirical rate",
                "tests": ["test_one", "test_two", "test_three"],
            },
        },
    }


def studio_files() -> dict[str, Any]:
    """The Founder Studio results files (and web_metrics.json, a local container run)."""
    siteiq_rows = {
        "type_a": {
            "siteiq_spearman": risk_ci(0.2222, 0.1111, 0.3333),
            "siteiq_minus_baseline_spearman": risk_ci(0.0222, 0.0111, 0.0333),
        },
        "type_b": {
            "siteiq_spearman": risk_ci(0.5555, 0.4444, 0.6666),
            "siteiq_minus_baseline_spearman": risk_ci(-0.0333, -0.0444, -0.0222),
        },
        "type_c": {
            "siteiq_spearman": risk_ci(0.3434, 0.2323, 0.4545),
            "siteiq_minus_baseline_spearman": risk_ci(0.0055, -0.0111, 0.0222),
        },
    }
    misspecified = {
        "condition": "misspecified",
        "n_worlds": 1200,
        "brier_dscr_year2_simulator": risk_ci(0.1444, 0.1344, 0.1544),
        "brier_dscr_year2_single_forecast": risk_ci(0.4555, 0.4455, 0.4655),
        "brier_dscr_year2_base_rate_in_sample": risk_ci(0.1666, 0.1566, 0.1766),
        "band_coverage_revenue": risk_ci(0.6222, 0.6122, 0.6322),
    }
    return {
        "studio_metro_ranking.json": {
            "configured_equals_top_five": True,
            "configured_metros": [{"metro_id": f"metro_{index}"} for index in range(5)],
            "metropolitan_areas_ranked": 380,
        },
        "studio_metro_coverage.json": {
            "summary": [
                {"data_label": "PUBLIC", "min_coverage_share": 0.6543},
                {"data_label": "PUBLIC", "min_coverage_share": 0.6987},
                {"data_label": "SYNTHETIC", "min_coverage_share": 0.9111},
            ]
        },
        "studio_path_metrics.json": {
            "settings": {"business_types": ["a", "b", "c", "d"], "targets": ["t1", "t2", "t3"]},
            "headline": {
                "path_found_rate_full": risk_ci(0.1234, 0.0987, 0.1456, n=410),
                "path_found_and_program_compliant_rate_full": risk_ci(0.0456, 0.0321, 0.0598, n=410),
            },
        },
        "studio_feasibility_metrics.json": {
            "settings": {"dscr_year": 2},
            "method": {"coverage_reference_if_well_specified": {"value": 0.7984}},
            "headline": [{"condition": "well_specified", "n_worlds": 1200}, misspecified, {"timing_ms": 3.79}],
        },
        "studio_evidence_metrics.json": {
            "provider": "template",
            "golden": {"scenarios_run": 18},
            "headline": {
                "narrative_numbers_traced_bound": {"k": 812, "n": 812, "rate": 1.0, "wilson_ci95": [0.9953, 1.0]}
            },
        },
        "studio_handoff_metrics.json": {
            "golden_founder_scenarios": {
                "package_numbers_read_back": 12345,
                "untraced_numbers_package_text": 0,
                "untraced_numbers_pdf_text": 0,
            },
            "all_cases": {"reached_human_checkpoint": ci(1.0, 1.0, 1.0, n=40), "decisions_recorded_without_a_human": 0},
        },
        "studio_siteiq_metrics.json": {"by_business_type": siteiq_rows},
        "studio_business_metrics.json": {
            "headline": {
                "time_to_complete_package_median_s": {"value": 5.432, "ci95": [5.123, 5.876], "n": 24},
                "evidence_coverage_rule_s": {
                    "studio": {"value": 0.8765, "ci_low": 0.8654, "ci_high": 0.8876, "scenarios": 24},
                    "baseline_b0": {"value": 0.0432, "ci_low": 0.0321, "ci_high": 0.0543, "scenarios": 24},
                },
            },
            "cost_per_package": {"llm_cost_usd_per_package": 0.0},
        },
        "web_metrics.json": {
            "runs": {
                "local-container": {
                    "target": {"kind": "local-container"},
                    "e2e": {"flows": [{"passed": True}, {"passed": True}, {"passed": False}]},
                    "axe": {"totals": {"scans": 14, "serious_violation_rules": 1, "critical_violation_rules": 0}},
                }
            }
        },
    }


def write_json(folder: Path, name: str, data: Any) -> None:
    """Write one results file (creating sub-folders such as injection/)."""
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_results(folder: Path) -> Path:
    """Write every fixture results file into folder and return it."""
    for name, data in {**lender_files(), **studio_files()}.items():
        write_json(folder, name, data)
    return folder


def fake_repo(root: Path, model_card: bool = True, ci_gate: bool = True) -> Path:
    """A repo root with (optionally) the credit model card and a CI workflow that runs the release gate."""
    if model_card:
        card = root / "docs/model_cards/credit_model_card.md"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text("# Credit model card (test fixture)\n", encoding="utf-8")
    workflow = root / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("jobs:\n  gate:\n    steps:\n      - run: make release-gate\n" if ci_gate else "jobs: {}\n")
    return root


def json_numbers(value: Any) -> list[float]:
    """Every numeric leaf of a JSON value, plus the length of every list and mapping."""
    if isinstance(value, dict):
        found = [float(len(value))] + [float(key) for key in value if key.isdigit()]
        return found + [number for item in value.values() for number in json_numbers(item)]
    if isinstance(value, list):
        return [float(len(value))] + [number for item in value for number in json_numbers(item)]
    if isinstance(value, bool) or not isinstance(value, int | float):
        return []
    return [float(value)]


def allowed_numbers(folder: Path) -> set[str]:
    """Every way a bullet may print a number from the results files in folder (4 or 1 decimals, counts, short)."""
    allowed = set(SMALL_COUNTS)
    for path in folder.rglob("*.json"):
        for number in json_numbers(json.loads(path.read_text(encoding="utf-8"))):
            size = abs(number)
            allowed |= {f"{size:.4f}", f"{size:.1f}", f"{size:g}"}
            if size.is_integer():
                allowed |= {f"{int(size):,}", str(int(size))}
    return allowed


def numbers_in(text: str) -> list[str]:
    """Number tokens in a bullet, after removing the fixed names and dates of STATIC_PHRASES."""
    for phrase in STATIC_PHRASES:
        text = text.replace(phrase, " ")
    return NUMBER.findall(text)
