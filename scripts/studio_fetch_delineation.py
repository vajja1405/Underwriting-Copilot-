"""Fill the real metros' CBSA code, official title, states and county FIPS codes from the Census files (Studio S1).

    uv run python scripts/studio_fetch_delineation.py            # download (or reuse the cache), update the YAMLs
    uv run python scripts/studio_fetch_delineation.py --dry-run  # show what would change, write nothing

What it does, in order:
1. Downloads the Census Bureau CBSA delineation file ("List 1", July 2023) and the Census Population Estimates
   Program CBSA totals file. Both go through the polite, caching client; both get a SHA-256 line in
   data/studio/checksums.jsonl and are saved under data/studio/reference/ (gitignored, never committed).
2. For every real metro config (data_label PUBLIC), finds its CBSA in the delineation file (by cbsa_code, or by
   cbsa_title_prefix the first time) and writes the official title, CBSA code, states (from the title), every county
   with its state and county FIPS code, and a delineation record (URL, date, file SHA-256, retrieval time). Only
   metros filled this way get verified: true. Traffic adapters for states the file does not list are disabled.
3. Ranks metropolitan statistical areas by the latest population estimate and writes
   results/studio_metro_ranking.json, so "the five largest metros" is checked against the file.

Nothing here is typed from memory: every code, title and number is read from the two files.
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from uwcopilot.common.files import append_checksum_log
from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.governance.provenance import RunProvenance, display_path
from uwcopilot.studio.data.cache import write_atomic
from uwcopilot.studio.data.http import FetchResult, PoliteClient
from uwcopilot.studio.data.paths import CHECKSUM_LOG, REFERENCE_DIR
from uwcopilot.studio.metros.delineation import (
    DelineationFile,
    DelineationRow,
    parse_delineation,
    read_xlsx_rows,
    rows_for_title_prefix,
    states_from_title,
)
from uwcopilot.studio.metros.loader import METROS_DIR, list_metros, load_metro
from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.metros.ranking import MetroPopulation, rank_metros

DELINEATION_URL = (
    "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/"
    "list1_2023.xlsx"
)
PEP_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/metro/totals/cbsa-est2025-alldata.csv"
RANKING_RESULTS = RESULTS_DIR / "studio_metro_ranking.json"
GENERATED_BY = "scripts/studio_fetch_delineation.py"
TOP_N = 5
TOP_LISTED = 10
MONTH_YEAR = re.compile(
    r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER) (\d{4})\s*$",
    re.IGNORECASE,
)
SOURCE_NOTE = re.compile(r"^Source: File prepared by (?P<publisher>.+?), based on")
YAML_HEADER = """# Metro config (Founder Studio S1). Schema: src/uwcopilot/studio/metros/models.py.
# The name, CBSA code, states and counties below were written by scripts/studio_fetch_delineation.py from the Census
# Bureau delineation file recorded under `delineation` (URL, date, SHA-256 of the file). Do not edit them by hand;
# re-run the script. Trade-area settings and enhancements are design choices of the project author. Without a data
# snapshot this metro runs in "limited mode" (see results/studio_metro_coverage.json).
"""


def download(client: PoliteClient, url: str, dataset: str) -> tuple[FetchResult, Path]:
    """Fetch url (or reuse the cache), save the body under data/studio/reference and log its SHA-256."""
    fetched = client.get(url)
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = REFERENCE_DIR / url.rsplit("/", 1)[1]
    write_atomic(path, fetched.body)
    action = "reused_cache" if fetched.from_cache else "downloaded"
    append_checksum_log(CHECKSUM_LOG, dataset, path, url, action)
    return fetched, path


def delineation_date(parsed: DelineationFile) -> str:
    """The delineation date printed at the end of the file's title, e.g. 'July 2023'."""
    match = MONTH_YEAR.search(parsed.title)
    if match is None:
        raise ValueError(f"no month and year at the end of the title {parsed.title!r}")
    return f"{match.group(1).capitalize()} {match.group(2)}"


def delineation_publisher(parsed: DelineationFile) -> str:
    """The publisher named in the file's 'Source:' note."""
    for note in parsed.notes:
        match = SOURCE_NOTE.match(note)
        if match:
            return match.group("publisher")
    raise ValueError("the delineation file has no 'Source: File prepared by ...' note")


def find_rows(parsed: DelineationFile, metro: MetroConfig) -> list[DelineationRow]:
    """The metro's county rows: by CBSA code when known, else by the title prefix."""
    if metro.cbsa_code:
        rows = [row for row in parsed.rows if row.cbsa_code == metro.cbsa_code]
        if not rows:
            raise ValueError(f"{metro.id}: CBSA {metro.cbsa_code} is not in the delineation file")
        return rows
    if not metro.cbsa_title_prefix:
        raise ValueError(f"{metro.id}: set cbsa_code or cbsa_title_prefix so the metro can be found in the file")
    return rows_for_title_prefix(parsed, metro.cbsa_title_prefix)


def county_entry(row: DelineationRow) -> dict[str, Any]:
    """One county as written to the metro YAML."""
    return {
        "state_fips": row.state_fips,
        "county_fips": row.county_fips,
        "name": row.county_name,
        "state_name": row.state_name,
        "central_or_outlying": row.central_or_outlying.lower(),
    }


def updated_config(
    raw: dict[str, Any],
    rows: list[DelineationRow],
    record: dict[str, Any],
    rank_note: str,
) -> dict[str, Any]:
    """The metro YAML mapping with name, CBSA code, states, counties, delineation and verified filled from the file."""
    title = rows[0].cbsa_title
    states = states_from_title(title)
    data = dict(raw)
    data["name"] = title
    data["verified"] = True
    data["states"] = states
    data["cbsa_code"] = rows[0].cbsa_code
    data["counties"] = [county_entry(row) for row in rows]
    data["delineation"] = record
    enhancements = []
    for item in raw.get("enhancements") or []:
        entry = dict(item)
        if entry.get("kind") == "traffic_state" and entry.get("adapter") not in states and entry.get("enabled", True):
            entry["enabled"] = False
            entry["note"] = (
                f"Disabled by {GENERATED_BY}: the delineation file lists no county of state {entry['adapter']} "
                "in this metro."
            )
        enhancements.append(entry)
    data["enhancements"] = enhancements
    data["note"] = rank_note
    return data


def rank_note(metro_cbsa: str, column: str, ranking: list[MetroPopulation], pep_file: str) -> str:
    """A plain sentence giving the metro's population rank (no population number is copied into the config)."""
    codes = [item.cbsa_code for item in ranking]
    if metro_cbsa not in codes:
        return f"CBSA {metro_cbsa} is not a metropolitan statistical area row in {pep_file}."
    rank = codes.index(metro_cbsa) + 1
    return (
        f"Rank {rank} of {len(codes)} metropolitan statistical areas by {column} in {pep_file} "
        f"(written by {GENERATED_BY}; see {display_path(RANKING_RESULTS)})."
    )


class QuotedCodesDumper(yaml.SafeDumper):
    """A YAML dumper that always quotes all-digit strings, so FIPS codes like 039 can never be read as numbers."""


def represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    """Quote digit-only strings; leave other strings to the default style."""
    style = "'" if value.isdigit() else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


QuotedCodesDumper.add_representer(str, represent_str)


def write_metro_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write the config with the generated header, keeping the key order."""
    body = yaml.dump(data, Dumper=QuotedCodesDumper, sort_keys=False, allow_unicode=True, width=120)
    write_atomic(path, (YAML_HEADER + body).encode("utf-8"))


def ranking_report(
    column: str,
    ranking: list[MetroPopulation],
    pep: FetchResult,
    configured: dict[str, str],
) -> dict[str, Any]:
    """The results/studio_metro_ranking.json body (without the provenance block)."""
    codes = [item.cbsa_code for item in ranking]
    top_codes = set(codes[:TOP_N])
    return {
        "rule": "v1 metros = the five largest US metropolitan statistical areas by population in the latest Census "
        "Bureau estimates (Studio plan)",
        "source": {
            "publisher": "U.S. Census Bureau, Population Estimates Program",
            "url": pep.url,
            "file": pep.url.rsplit("/", 1)[1],
            "sha256": pep.sha256,
            "retrieved_at_utc": pep.retrieved_at.isoformat(timespec="seconds"),
            "column_ranked": column,
            "rows_ranked": "CBSA totals with LSAD 'Metropolitan Statistical Area' (no division or county rows)",
        },
        "metropolitan_areas_ranked": len(ranking),
        "top": [
            {"rank": index + 1, "cbsa_code": item.cbsa_code, "name": item.name, "population": item.population}
            for index, item in enumerate(ranking[:TOP_LISTED])
        ],
        "configured_metros": [
            {
                "metro_id": metro_id,
                "cbsa_code": code,
                "rank": codes.index(code) + 1 if code in codes else None,
            }
            for metro_id, code in sorted(configured.items())
        ],
        "configured_equals_top_five": set(configured.values()) == top_codes,
    }


def main(argv: list[str] | None = None) -> int:
    """Run the three steps; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the changes, write no config or results")
    parser.add_argument("--metros-dir", type=Path, default=METROS_DIR)
    args = parser.parse_args(argv)

    run = RunProvenance.start(GENERATED_BY)
    client = PoliteClient()
    deli_fetch, deli_path = download(client, DELINEATION_URL, "census_cbsa_delineation_list1_2023")
    parsed = parse_delineation(read_xlsx_rows(deli_path))
    pep_fetch, _ = download(client, PEP_URL, "census_pep_cbsa_totals")
    column, ranking = rank_metros(pep_fetch.body)
    pep_file = PEP_URL.rsplit("/", 1)[1]

    source = {
        "publisher": delineation_publisher(parsed),
        "title": parsed.title,
        "date": delineation_date(parsed),
        "url": DELINEATION_URL,
        "page_or_table": "",
    }
    configured: dict[str, str] = {}
    for metro_id in list_metros(args.metros_dir):
        metro = load_metro(metro_id, args.metros_dir)
        if metro.data_label != "PUBLIC":
            continue
        rows = find_rows(parsed, metro)
        code = rows[0].cbsa_code
        configured[metro_id] = code
        record = {
            "source": {
                **source,
                "page_or_table": f"{deli_path.name}, first sheet, the {len(rows)} rows with CBSA Code {code}",
            },
            "file_name": deli_path.name,
            "file_sha256": deli_fetch.sha256,
            "retrieved_at_utc": deli_fetch.retrieved_at.isoformat(timespec="seconds"),
            "generated_by": GENERATED_BY,
        }
        path = args.metros_dir / f"{metro_id}.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        data = updated_config(raw, rows, record, rank_note(code, column, ranking, pep_file))
        MetroConfig.model_validate(data)  # never write a config the loader would refuse
        print(f"{metro_id}: CBSA {code} {rows[0].cbsa_title!r}, {len(rows)} counties, states {data['states']}")
        if not args.dry_run:
            write_metro_yaml(path, data)
            load_metro(metro_id, args.metros_dir)

    report = ranking_report(column, ranking, pep_fetch, configured)
    print(
        f"Top {TOP_N} by {column}: "
        + "; ".join(f"{item['cbsa_code']} {item['name']}" for item in report["top"][:TOP_N])
    )
    print(f"Configured real metros equal the top {TOP_N}: {report['configured_equals_top_five']}")
    if args.dry_run:
        return 0
    report["provenance"] = run.finish(
        corpus_checksum="not applicable (no retrieval corpus is used)",
        data_snapshots={
            "census_cbsa_delineation": {"url": DELINEATION_URL, "sha256": deli_fetch.sha256},
            "census_pep_cbsa_totals": {"url": PEP_URL, "sha256": pep_fetch.sha256},
        },
    )
    report["written_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    RANKING_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(RANKING_RESULTS, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(f"Wrote {display_path(RANKING_RESULTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
