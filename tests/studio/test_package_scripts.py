"""The S6 scripts: the SBA page reader (on a recorded HTML fixture, no network) and the handoff metrics script on a
small subset with designed screening (no sanctions list, no LLM)."""

import json
from pathlib import Path

import pytest

import eval_studio_handoff
import studio_fetch_sba_plan_guide as sba_script
from uwcopilot.studio.package.sba import sba_sections

FIXTURE_PAGE = """
<h1>Plan your business</h1>
<h2>Write your business plan</h2>
<h4 id="common-traditional-business-plan-sections">Common traditional business plan sections</h4>
<h5 class="wp-block-heading">Executive summary</h5><p>...</p>
<h5 class="x">Company&nbsp;description</h5>
<h5>Funding <em>request</em></h5>
<h4>Lean startup plan</h4>
<h5>Key partnerships</h5>
"""


def test_the_section_names_are_the_h5_headings_under_the_list_heading() -> None:
    assert sba_script.section_names(FIXTURE_PAGE) == ["Executive summary", "Company description", "Funding request"]
    assert sba_script.page_context(FIXTURE_PAGE) == ("Plan your business", "Write your business plan")


def test_a_page_without_the_list_heading_is_refused() -> None:
    with pytest.raises(sba_script.SectionListNotFound):
        sba_script.section_names("<h4>Something else</h4><h5>Executive summary</h5>")


def test_the_committed_record_was_written_by_the_script() -> None:
    record = json.loads(sba_script.RECORD_FILE.read_text(encoding="utf-8"))
    assert record["generated_by"] == sba_script.GENERATED_BY and record["list_heading"] == sba_script.LIST_HEADING
    assert record["requested_url"] == sba_script.SBA_URL and sba_sections() == record["sections"]


def test_the_handoff_metrics_script_writes_its_results(tmp_path: Path) -> None:
    output = tmp_path / "studio_handoff_metrics.json"
    code = eval_studio_handoff.main(
        [
            "--screening",
            "ground_truth",
            "--limit-applications",
            "2",
            "--limit-golden",
            "1",
            "--n-paths",
            "200",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert (
        report["data_label"] == "SYNTHETIC" and report["provenance"]["generated_by"] == eval_studio_handoff.GENERATED_BY
    )
    assert report["startup_applications"]["cases"] == 2 and report["golden_founder_scenarios"]["cases"] == 1
    assert report["all_cases"]["decisions_recorded_without_a_human"] == 0
    assert report["golden_founder_scenarios"]["agreement_with_labels"].startswith("not applicable")
    block = report["startup_applications"]["recommendation_agreement_with_labels"]
    assert {"value", "ci_low", "ci_high", "n", "k", "wilson_ci95"} <= set(block)
