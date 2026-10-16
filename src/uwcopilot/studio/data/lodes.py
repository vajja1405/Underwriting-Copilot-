"""Census LEHD LODES 8 workplace jobs per tract: the daytime-worker layer of a real metro. No key needed.

    jobs = fetch_state_jobs(client, "il", {"17031", "17043"})
    jobs.tract_jobs["17031839100"]   # jobs located in that tract (all jobs, 2023)
    jobs.record                      # SourceRecord of the WAC file (URL, request date, SHA-256, vintage)

What is read (facts from the LODES Technical Document 8.4, Rev. 20251203, and the LEHD data page, both read on
2026-10-13 and listed in docs/studio/data_sources.md):
- the Workplace Area Characteristics (WAC) file of a state, https://lehd.ces.census.gov/data/lodes/LODES8/<st>/wac/
  <st>_wac_S000_JT00_2023.csv.gz: one row per 2020 census block with jobs, w_geocode (the 15-digit block code) and
  C000 (total number of jobs). S000 = all workers, JT00 = all jobs. These are the file and columns the lead
  engineer specified for this layer;
- the state's checksum file lodes_<st>.sha256sum, which lists the SHA-256 of the uncompressed CSV. The decompressed
  file must match it, or the step fails;
- the state's version.txt, which gives the data vintage (for example "Data Vintage: 20251202_1657") and the format
  version. The LEHD data page asks users to cite the file name, the vintage, https://lehd.ces.census.gov/data/lodes
  and the access date; the SourceRecord keeps all four.

A tract's value is the sum of C000 over its blocks (the first 11 digits of a block code are its 2020 tract code,
the same tract vintage as the Gazetteer tract layer). It counts JOBS at workplaces in the tract, not distinct
people (one person can hold two jobs), so Studio labels it "jobs located in the tract" and uses it as the daytime
worker proxy. A tract with no block in the file gets no value (UNKNOWN), never zero: the technical document does
not say that a missing block means zero jobs. One exception, made by the snapshot step and labelled per tract
(WORKERS_BASIS_FIELD = WATER_ONLY_BASIS): a tract whose land area is 0 in the Census Gazetteer tract layer (water only)
and that has no block in the file gets 0 jobs, because it has no land on which a workplace could be located.
"""

import csv
import gzip
import hashlib
import io
import re
from dataclasses import dataclass, field

from uwcopilot.studio.data.errors import UpstreamError
from uwcopilot.studio.data.http import FetchResult, PoliteClient
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord

HOST = "lehd.ces.census.gov"
BASE_URL = f"https://{HOST}/data/lodes/LODES8"
YEAR = 2023
SEGMENT = "S000"
JOB_TYPE = "JT00"
JOBS_COLUMN = "C000"
BLOCK_COLUMN = "w_geocode"
GZIP_MAGIC = b"\x1f\x8b"
# Per-tract field that says how a daytime-worker value was obtained when it is not a sum of the file's blocks.
WORKERS_BASIS_FIELD = "daytime_workers_basis"
WATER_ONLY_BASIS = "water_only_no_block"
MAX_UNZIPPED_BYTES = 500 * 1024 * 1024
CHECKSUM_LINE = re.compile(r"^(?P<sha>[0-9a-f]{64})\s+\*?(?P<name>\S+)$")
VINTAGE_LINE = re.compile(r"^Data Vintage:\s*(?P<vintage>\S+)", re.MULTILINE)
FORMAT_LINE = re.compile(r"^Release Format Version\s*(?P<format>\S+)", re.MULTILINE)
CITATION = (
    'U.S. Census Bureau, "{file}," LEHD Origin-Destination Employment Statistics, {vintage}, '
    "https://lehd.ces.census.gov/data/lodes, accessed on {date}."
)
SOURCE = SourceInfo(
    source_id="lehd_lodes8_wac",
    publisher="U.S. Census Bureau, Longitudinal Employer-Household Dynamics (LEHD)",
    dataset=f"LODES 8 Workplace Area Characteristics (WAC), segment {SEGMENT}, job type {JOB_TYPE}, {YEAR}",
    license_note=(
        "No license or terms-of-use text was found on https://lehd.ces.census.gov/data/ (read 2026-10-13); terms "
        'not recorded. The page\'s citation format is used: U.S. Census Bureau, "<file>," LEHD Origin-Destination '
        "Employment Statistics, <vintage>, https://lehd.ces.census.gov/data/lodes, accessed on <date>."
    ),
)


def wac_file_name(state: str, year: int = YEAR) -> str:
    """'il_wac_S000_JT00_2023.csv.gz' for a two-letter postal code."""
    if len(state) != 2 or not state.isalpha():
        raise ValueError(f"state must be a two-letter postal code (got {state!r})")
    return f"{state.lower()}_wac_{SEGMENT}_{JOB_TYPE}_{year}.csv.gz"


def wac_url(state: str, year: int = YEAR) -> str:
    """The WAC file's URL."""
    return f"{BASE_URL}/{state.lower()}/wac/{wac_file_name(state, year)}"


def checksum_url(state: str) -> str:
    """The state's published checksum list."""
    return f"{BASE_URL}/{state.lower()}/lodes_{state.lower()}.sha256sum"


def version_url(state: str) -> str:
    """The state's version file."""
    return f"{BASE_URL}/{state.lower()}/version.txt"


def check_gzip(fetched: FetchResult) -> None:
    """Accept only a gzip body (an http.Accept check: an HTML error page is never cached)."""
    if not fetched.body.startswith(GZIP_MAGIC):
        raise UpstreamError(HOST, "the WAC answer is not a gzip file")


def check_text(fetched: FetchResult) -> None:
    """Accept a plain-text answer (checksum list, version file), not an HTML page."""
    if b"<html" in fetched.body[:500].lower():
        raise UpstreamError(HOST, "answered with an HTML page instead of a text file")


def parse_checksums(text: str) -> dict[str, str]:
    """{file name: SHA-256} from a sha256sum list ('<hash>  <name>' per line)."""
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        match = CHECKSUM_LINE.match(line.strip())
        if match:
            checksums[match.group("name")] = match.group("sha")
    return checksums


def parse_version(text: str) -> tuple[str, str]:
    """(data vintage, format version) from version.txt; UpstreamError when either line is missing."""
    vintage = VINTAGE_LINE.search(text)
    version = FORMAT_LINE.search(text)
    if vintage is None or version is None:
        raise UpstreamError(HOST, "version.txt has no 'Data Vintage' or 'Release Format Version' line")
    return vintage.group("vintage"), version.group("format")


@dataclass
class TractJobs:
    """Jobs per tract read from one WAC file, and what was read."""

    tract_jobs: dict[str, int] = field(default_factory=dict)
    rows_read: int = 0
    rows_kept: int = 0
    min_jobs_in_file: int | None = None
    createdates: set[str] = field(default_factory=set)
    uncompressed_sha256: str = ""


def tract_jobs(body: bytes, county_geoids: set[str]) -> TractJobs:
    """Sum C000 per tract (first 11 digits of w_geocode) for blocks in the given counties (first 5 digits)."""
    result = TractJobs()
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as handle:
        raw = handle.read(MAX_UNZIPPED_BYTES + 1)
    if len(raw) > MAX_UNZIPPED_BYTES:
        raise UpstreamError(HOST, "the WAC file is larger than expected")
    result.uncompressed_sha256 = hashlib.sha256(raw).hexdigest()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    missing = [name for name in (BLOCK_COLUMN, JOBS_COLUMN) if name not in (reader.fieldnames or [])]
    if missing:
        raise UpstreamError(HOST, f"the WAC file is missing columns {missing}")
    for row in reader:
        result.rows_read += 1
        jobs = int(row[JOBS_COLUMN])
        result.min_jobs_in_file = jobs if result.min_jobs_in_file is None else min(result.min_jobs_in_file, jobs)
        if row.get("createdate"):
            result.createdates.add(row["createdate"])
        block = row[BLOCK_COLUMN].strip()
        if len(block) != 15 or not block.isdigit():
            raise UpstreamError(HOST, f"unexpected block code {block!r} in the WAC file")
        if block[:5] in county_geoids:
            result.rows_kept += 1
            tract = block[:11]
            result.tract_jobs[tract] = result.tract_jobs.get(tract, 0) + jobs
    return result


@dataclass(frozen=True)
class StateJobs:
    """One state's jobs per tract (metro counties only) with the provenance and the checksum verification."""

    state: str
    jobs: TractJobs
    record: SourceRecord
    published_sha256: str
    vintage: str
    format_version: str
    support_records: list[SourceRecord]

    @property
    def citation(self) -> str:
        """The citation the LEHD data page asks for, with this file, vintage and access date."""
        return CITATION.format(
            file=wac_file_name(self.state).removesuffix(".gz"),
            vintage=self.vintage,
            date=self.record.retrieved_at.date().isoformat(),
        )


def fetch_state_jobs(client: PoliteClient, state: str, county_geoids: set[str], year: int = YEAR) -> StateJobs:
    """Download one state's WAC file, check it against the published SHA-256, and sum jobs per tract."""
    version_answer = client.get(version_url(state), accept=check_text)
    vintage, format_version = parse_version(version_answer.text())
    checksum_answer = client.get(checksum_url(state), accept=check_text)
    name = wac_file_name(state, year).removesuffix(".gz")
    published = parse_checksums(checksum_answer.text()).get(name)
    if published is None:
        raise UpstreamError(HOST, f"{name} is not in {checksum_url(state)}")
    fetched = client.get(wac_url(state, year), accept=check_gzip)
    jobs = tract_jobs(fetched.body, county_geoids)
    if jobs.uncompressed_sha256 != published:
        raise UpstreamError(HOST, f"{name}: SHA-256 of the file does not match the published checksum")
    record = SOURCE.record(
        fetched,
        vintage=f"jobs year {year}; LODES data vintage {vintage}; format version {format_version}",
        geography=f"state {state.upper()}",
    )
    support = [
        SOURCE.record(version_answer, vintage=vintage, geography=f"state {state.upper()} version.txt"),
        SOURCE.record(checksum_answer, vintage=vintage, geography=f"state {state.upper()} sha256sum"),
    ]
    return StateJobs(state.lower(), jobs, record, published, vintage, format_version, support)
