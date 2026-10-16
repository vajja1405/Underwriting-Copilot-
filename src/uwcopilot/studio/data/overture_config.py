"""The Overture places settings and the reviewed category mapping (config/studio/overture_*.yaml), as typed models.

Two files:
- config/studio/overture_places.yaml: the ONE pinned release, the extraction settings (DESIGN_CHOICE values) and the
  license and attribution facts read from Overture's documentation pages (with URL, retrieval time and SHA-256).
- config/studio/overture_category_map.yaml: which Overture category counts as which profile tag (for example
  car_wash -> amenity=car_wash), one reviewed row per category with a note.

The mapping rule (CategoryMap.tags_for): walk a place's taxonomy.hierarchy from the most specific category to the
most general one; the first category with a row decides the place's tags. A row with no tags means "reviewed and not
counted" and stops the walk. A hierarchy without any row is "unmapped" (None): ignored and counted. A row with
merge_within_miles is a facility row (airport, hospital, university): nearby places it decides are merged into one.
"""

import hashlib
import re
from collections.abc import Sequence
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.studio.profiles.models import OSM_TAG

SETTINGS_FILE: Path = CONFIG_DIR / "studio" / "overture_places.yaml"
CATEGORY_MAP_FILE: Path = CONFIG_DIR / "studio" / "overture_category_map.yaml"
CATEGORY_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
RELEASE_ID = re.compile(r"^\d{4}-\d{2}-\d{2}\.\d+$")


class StrictModel(BaseModel):
    """Frozen model that refuses unknown fields (a typo in YAML fails instead of being ignored)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ReleasePin(StrictModel):
    """The pinned Overture release and where its places files are."""

    id: str
    release_date: str
    schema_version: str
    public_days_max: int = Field(gt=0)
    note: str = Field(min_length=1)
    s3_https_base: str
    bucket: str
    places_prefix: str

    @field_validator("id")
    @classmethod
    def release_id_format(cls, value: str) -> str:
        """Overture release ids look like 2026-09-23.1 (date, then a patch number)."""
        if not RELEASE_ID.match(value):
            raise ValueError(f"release id must look like YYYY-MM-DD.N (got {value!r})")
        return value

    @model_validator(mode="after")
    def prefix_names_the_release(self) -> "ReleasePin":
        """The places prefix must point into the pinned release (no silent mix of two releases)."""
        if not self.places_prefix.startswith(f"release/{self.id}/theme=places/"):
            raise ValueError("places_prefix must start with release/<id>/theme=places/")
        if not self.s3_https_base.startswith("https://"):
            raise ValueError("s3_https_base must be an https:// URL")
        return self

    def retire_on(self) -> date:
        """The last day the pin may be used: release_date plus public_days_max (Overture's retention policy)."""
        return date.fromisoformat(self.release_date) + timedelta(days=self.public_days_max)

    @property
    def places_url(self) -> str:
        """The HTTPS URL of the release's places folder."""
        return f"{self.s3_https_base}/{self.places_prefix}"


class NumberSetting(StrictModel):
    """A numeric extraction setting with its label and the reason for it."""

    value: float
    status: str
    note: str = Field(min_length=1)


class ListSetting(StrictModel):
    """A list extraction setting with its label and the reason for it."""

    values: list[str]
    status: str
    note: str = Field(min_length=1)


class NoteOnly(StrictModel):
    """A documented fact about the extraction that has no value to set."""

    note: str = Field(min_length=1)


class ExtractSettings(StrictModel):
    """How a metro's extract is made: the confidence filter and the excluded operating statuses."""

    min_confidence: NumberSetting
    exclude_operating_status: ListSetting
    coordinates: NoteOnly
    stored_fields: NoteOnly


class LicenseFacts(StrictModel):
    """License and attribution facts as read from the Overture attribution page and places guide."""

    summary: str = Field(min_length=1)
    licenses_by_source: dict[str, list[str]]
    attribution: str = Field(min_length=1)
    foursquare_notice_url: str
    retention: str = Field(min_length=1)


class DocSource(StrictModel):
    """One documentation page the facts above were read from."""

    title: str
    url: str
    retrieved_at_utc: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OvertureSettings(StrictModel):
    """config/studio/overture_places.yaml."""

    release: ReleasePin
    extract: ExtractSettings
    license: LicenseFacts
    sources: list[DocSource] = Field(min_length=1)

    def license_note(self) -> str:
        """The text stored in every Overture SourceRecord.license_note."""
        pages = ", ".join(f"{item.url} (retrieved {item.retrieved_at_utc[:10]})" for item in self.sources[:2])
        return (
            f"{self.license.summary} Attribution: {self.license.attribution} Retention: {self.license.retention} "
            f"Read from: {pages}."
        )


class MapRow(StrictModel):
    """One reviewed mapping row: an Overture category and the profile tags it counts as (none = not counted).

    merge_within_miles (optional, DESIGN_CHOICE) marks a facility category: one airport, hospital or campus appears
    in Overture as many places filed under the same category, so places decided by this row that lie within this
    distance of an already kept place of the row are merged into it (overture_places.merge_facilities).
    """

    category: str
    tags: list[str]
    note: str = Field(min_length=1)
    merge_within_miles: float | None = Field(default=None, gt=0, le=5)

    @model_validator(mode="after")
    def merge_needs_tags(self) -> "MapRow":
        """Only a counted row can merge places (a row with no tags keeps nothing to merge)."""
        if self.merge_within_miles is not None and not self.tags:
            raise ValueError(f"row {self.category!r}: merge_within_miles needs tags")
        return self

    @field_validator("category")
    @classmethod
    def category_is_snake_case(cls, value: str) -> str:
        """Overture category names are snake_case."""
        if not CATEGORY_NAME.match(value):
            raise ValueError(f"category must be snake_case (got {value!r})")
        return value

    @field_validator("tags")
    @classmethod
    def tags_are_valid(cls, tags: list[str]) -> list[str]:
        """Every tag must be a valid profile tag filter (the same pattern the profile model uses)."""
        bad = [tag for tag in tags if not OSM_TAG.match(tag)]
        if bad:
            raise ValueError(f"invalid tag filters {bad}")
        return tags


class UnmappedTag(StrictModel):
    """A profile tag with no Overture category, and why."""

    tag: str
    reason: str = Field(min_length=1)


class CategoryMap(StrictModel):
    """config/studio/overture_category_map.yaml."""

    reviewed_on: str
    release: str
    rows: list[MapRow] = Field(min_length=1)
    unmapped_profile_tags: list[UnmappedTag] = Field(default_factory=list)

    @model_validator(mode="after")
    def categories_are_unique(self) -> "CategoryMap":
        """One row per category, and no tag both mapped and listed as unmapped."""
        names = [row.category for row in self.rows]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"categories with more than one row: {duplicates}")
        both = sorted(self.mapped_tags() & {item.tag for item in self.unmapped_profile_tags})
        if both:
            raise ValueError(f"tags both mapped and listed as unmapped: {both}")
        return self

    def by_category(self) -> dict[str, MapRow]:
        """{category: row}."""
        return {row.category: row for row in self.rows}

    def mapped_tags(self) -> set[str]:
        """Every tag at least one row produces."""
        return {tag for row in self.rows for tag in row.tags}

    def merge_distances(self) -> dict[str, float]:
        """{row category: merge distance in miles} for the facility rows (see MapRow.merge_within_miles)."""
        return {row.category: row.merge_within_miles for row in self.rows if row.merge_within_miles is not None}

    def search_categories(self) -> list[str]:
        """Categories whose rows produce tags (the extraction query keeps places with one of them in the hierarchy)."""
        return sorted(row.category for row in self.rows if row.tags)

    def match(self, hierarchy: Sequence[str] | None) -> MapRow | None:
        """The row that decides a place's tags (most specific category first), or None when no row applies."""
        rows = self.by_category()
        for category in reversed(list(hierarchy or [])):
            if category in rows:
                return rows[category]
        return None

    def tags_for(self, hierarchy: Sequence[str] | None) -> tuple[str, ...] | None:
        """The place's tags: None = unmapped (no row), () = reviewed and not counted, else the row's tags."""
        row = self.match(hierarchy)
        return None if row is None else tuple(row.tags)


def read_yaml(path: Path) -> dict:
    """A YAML file as a dict (safe loader)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must hold a mapping")
    return data


def file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes (recorded so an extract names the exact mapping it used)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=4)
def load_settings(path: Path = SETTINGS_FILE) -> OvertureSettings:
    """The pinned release and extraction settings."""
    return OvertureSettings.model_validate(read_yaml(path))


@lru_cache(maxsize=4)
def load_category_map(path: Path = CATEGORY_MAP_FILE) -> CategoryMap:
    """The reviewed category mapping."""
    return CategoryMap.model_validate(read_yaml(path))
