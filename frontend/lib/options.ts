/**
 * Turn the server's `metros` / `business_types` lists into display labels.
 *
 * The Studio loaders are still being built, so the exact item shape is not fixed yet. This accepts plain strings
 * or objects with a name-like field (name, display_name, label, title, id) and an optional mode/status field
 * (for example "limited"), and never throws on an unexpected shape.
 */

export interface OptionLabel {
  key: string;
  label: string;
  /** For example "limited mode" or "ready"; null when the server did not say. */
  detail: string | null;
}

const NAME_FIELDS = ["display_name", "name", "label", "title", "id", "slug"] as const;
const DETAIL_FIELDS = ["mode", "status", "coverage_mode"] as const;

/** Readable wording for known metro modes; other values are shown as sent. */
const DETAIL_LABELS: Record<string, string> = {
  limited: "limited mode",
  synthetic: "SYNTHETIC",
};

function firstString(record: Record<string, unknown>, fields: readonly string[]): string | null {
  for (const field of fields) {
    const value = record[field];
    if (typeof value === "string" && value.trim() !== "") {
      return value.trim();
    }
  }
  return null;
}

function describeDetail(detail: string): string {
  return DETAIL_LABELS[detail] ?? detail.replaceAll("_", " ");
}

export function toOptionLabels(items: readonly unknown[] | null | undefined): OptionLabel[] {
  if (!Array.isArray(items)) {
    return [];
  }
  const labels: OptionLabel[] = [];
  items.forEach((item, index) => {
    if (typeof item === "string" && item.trim() !== "") {
      labels.push({ key: `${index}-${item}`, label: item.trim(), detail: null });
    } else if (typeof item === "object" && item !== null) {
      const record = item as Record<string, unknown>;
      const label = firstString(record, NAME_FIELDS);
      if (label !== null) {
        const detail = firstString(record, DETAIL_FIELDS);
        const detailText = detail === null ? null : describeDetail(detail);
        // Skip a detail the name already says, e.g. "Synthetic Metro (SYNTHETIC)" with mode "synthetic".
        const redundant = detailText !== null && label.toLowerCase().includes(detailText.toLowerCase());
        labels.push({ key: `${index}-${label}`, label, detail: redundant ? null : detailText });
      }
    }
  });
  return labels;
}
