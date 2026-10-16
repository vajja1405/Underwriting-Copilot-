"""Identity checks shared by both lines: business name, FEIN and owners agree across documents.

- name_consistency / fein_consistency (value_consistency): every document that prints the business name (or the
  FEIN) is grouped by its comparison key (normalize.canonical_name / canonical_fein). One group means the documents
  agree. Otherwise the majority group is the one printed on the most documents (a tie goes to the group holding
  the application's value), and every other group is an issue: the majority evidence on one side and the odd
  documents on the other. Fewer than two documents with a value means the check cannot be evaluated.
- owner_consistency (owner_issues): application owners are matched to the other ownership document (formation
  members for credit, the ownership statement for insurance) by canonical name, then leftover owners are paired
  when their names are within edit distance 2. Issues: an ownership percentage that differs by more than 5.0
  points (NBC-6.1; Harborline gives no tolerance, so the same 5.0 points is used for both lines), owners missing
  from either document, a name that differs, and a different control person when both documents name one.
  For credit, NBC-6.1 also requires the application's percentages to add up to 100% (within 0.5 points for
  printed rounding); a wrong total fails the check but is not an issue (see owner_consistency).

Names are untrusted free text: messages say which documents disagree, never the names themselves. The names are
in the evidence.
"""

from collections.abc import Callable

from uwcopilot.docintel.check_models import CheckIssue, CheckResult, Evidence, cite, make_result
from uwcopilot.docintel.normalize import canonical_name, edit_distance
from uwcopilot.docintel.schemas import ExtractedOwner

# NBC-6.1: "A difference of more than 5 percentage points for any owner ... is a referral".
OWNERSHIP_DIFFERENCE_POINTS = 5.0
# Owner names this close are treated as the same person with a differently written name.
OWNER_NAME_MAX_EDITS = 2
# NBC-6.1: the application's percentages must add up to 100%; 0.5 points allows for printed rounding (3 x 33.3%).
OWNERSHIP_TOTAL_TOLERANCE_POINTS = 0.5


def group_by_key(sources: list[Evidence], key_of: Callable[[str], str]) -> dict[str, list[Evidence]]:
    """Evidence grouped by comparison key, in first-seen order (values with an empty key are dropped)."""
    groups: dict[str, list[Evidence]] = {}
    for item in sources:
        key = key_of(str(item.value))
        if key:
            groups.setdefault(key, []).append(item)
    return groups


def doc_ids_of(items: list[Evidence]) -> list[str]:
    """Distinct document ids of the evidence, in first-seen order."""
    seen: list[str] = []
    for item in items:
        if item.doc_id is not None and item.doc_id not in seen:
            seen.append(item.doc_id)
    return seen


def group_rank(items: list[Evidence]) -> tuple[int, int]:
    """(number of documents, 1 if the application is in the group else 0): the larger rank wins the majority."""
    has_application = any(item.field.startswith("application.") for item in items)
    return len(doc_ids_of(items)), 1 if has_application else 0


def majority_key(groups: dict[str, list[Evidence]]) -> str:
    """The group printed on the most documents; ties go to the group with the application's value."""
    best = list(groups)[0]
    for key in groups:
        if group_rank(groups[key]) > group_rank(groups[best]):
            best = key
    return best


def value_consistency(
    check_id: str,
    refs: list[str],
    label: str,
    sources: list[Evidence],
    key_of: Callable[[str], str],
    show_values: bool,
) -> CheckResult:
    """Compare one identity value (business name or FEIN) across documents; see the module docstring."""
    groups = group_by_key(sources, key_of)
    all_items = [item for items in groups.values() for item in items]
    documents = doc_ids_of(all_items)
    values = {"documents": documents, "distinct_values": len(groups)}
    if len(documents) < 2:
        message = f"Fewer than two documents show a {label}; consistency cannot be checked."
        return make_result(check_id, refs, "not_evaluated", message, values, all_items)
    if len(groups) == 1:
        message = f"The {label} is the same on all {len(documents)} documents that show it."
        return make_result(check_id, refs, "pass", message, values, all_items)
    main = majority_key(groups)
    main_items = groups[main]
    issues = []
    for key, odd_items in groups.items():
        if key == main:
            continue
        odd_docs = doc_ids_of(odd_items)
        shown = f" ({odd_items[0].value} instead of {main_items[0].value})" if show_values else ""
        issues.append(
            CheckIssue(
                issue="value_differs",
                entity="business",
                fields=sorted({item.field for item in main_items + odd_items}),
                side_a=main_items,
                side_b=odd_items,
                data={
                    "majority_key": main,
                    "odd_key": key,
                    "majority_value": main_items[0].value,
                    "odd_value": odd_items[0].value,
                    "majority_doc_ids": doc_ids_of(main_items),
                    "odd_doc_ids": odd_docs,
                    "group_count": len(groups),
                },
                message=f"The {label} on {', '.join(odd_docs)} differs from the one on "
                f"{len(doc_ids_of(main_items))} other document(s){shown}.",
            )
        )
    values["majority_doc_ids"] = doc_ids_of(main_items)
    values["odd_doc_ids"] = [doc for issue in issues for doc in issue.data["odd_doc_ids"]]
    message = f"The {label} differs between documents: " + " ".join(issue.message for issue in issues)
    return make_result(check_id, refs, "fail", message, values, [], issues)


def owner_evidence(owner: ExtractedOwner, prefix: str, index: int, doc_id: str | None) -> list[Evidence]:
    """Evidence for an owner's name, ownership percentage and control-person flag (key "O<index>")."""
    evidence = []
    for name in ("name", "ownership_pct", "is_control_person"):
        item = cite(getattr(owner, name), f"{prefix}.{name}", f"O{index}", doc_id)
        if item is not None:
            evidence.append(item)
    return evidence


def pct_of(owner: ExtractedOwner) -> float | None:
    """The owner's ownership percentage, if found."""
    return None if owner.ownership_pct.value is None else float(owner.ownership_pct.value)


def match_owners(
    left: list[ExtractedOwner], right: list[ExtractedOwner]
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair owners by canonical name, then by edit distance <= 2. Returns (pairs, unmatched left, unmatched right).

    Indices are 0-based positions in each list.
    """
    pairs: list[tuple[int, int]] = []
    free_right = list(range(len(right)))
    unmatched_left: list[int] = []
    for i, owner in enumerate(left):
        key = canonical_name(owner.name.value)
        match = next((j for j in free_right if key and canonical_name(right[j].name.value) == key), None)
        if match is None:
            unmatched_left.append(i)
        else:
            pairs.append((i, match))
            free_right.remove(match)
    still_unmatched: list[int] = []
    for i in unmatched_left:
        key = canonical_name(left[i].name.value)
        distances = [(edit_distance(key, canonical_name(right[j].name.value)), j) for j in free_right]
        close = [item for item in distances if key and item[0] <= OWNER_NAME_MAX_EDITS]
        if close:
            j = min(close)[1]
            pairs.append((i, j))
            free_right.remove(j)
        else:
            still_unmatched.append(i)
    return pairs, still_unmatched, free_right


def control_evidence(owners: list[ExtractedOwner], prefix: str, doc_id: str | None) -> list[Evidence]:
    """Evidence for every owner marked as the control person."""
    evidence = []
    for index, owner in enumerate(owners, start=1):
        if owner.is_control_person.value is True:
            evidence.extend(owner_evidence(owner, prefix, index, doc_id))
    return evidence


def control_names(owners: list[ExtractedOwner]) -> set[str]:
    """Canonical names of the owners marked as the control person."""
    return {canonical_name(owner.name.value) for owner in owners if owner.is_control_person.value is True}


def owner_issues(
    application_owners: list[ExtractedOwner],
    application_doc: str | None,
    other_owners: list[ExtractedOwner],
    other_doc: str | None,
    other_prefix: str,
    min_pct_listed: float,
) -> list[CheckIssue]:
    """Ownership disagreements between the application and the other ownership document (module docstring).

    An application owner below min_pct_listed percent is not expected on the other document (the Harborline
    ownership statement lists only owners of 25% or more); use 0.0 when every owner must be listed.
    """
    issues: list[CheckIssue] = []
    pairs, app_only, other_only = match_owners(application_owners, other_owners)
    for i, j in pairs:
        app_side = owner_evidence(application_owners[i], "application.owners", i + 1, application_doc)
        other_side = owner_evidence(other_owners[j], other_prefix, j + 1, other_doc)
        entity = f"owner:O{i + 1}"
        app_name = canonical_name(application_owners[i].name.value)
        other_name = canonical_name(other_owners[j].name.value)
        if app_name != other_name:
            issues.append(
                CheckIssue(
                    issue="name_difference",
                    entity=entity,
                    fields=["application.owners.name", f"{other_prefix}.name"],
                    side_a=app_side,
                    side_b=other_side,
                    data={"edit_distance": edit_distance(app_name, other_name), "other_doc_id": other_doc},
                    message=f"Owner O{i + 1}'s name is written differently on {other_doc}.",
                )
            )
        app_pct, other_pct = pct_of(application_owners[i]), pct_of(other_owners[j])
        if app_pct is not None and other_pct is not None:
            difference = round(abs(app_pct - other_pct), 1)
            if difference > OWNERSHIP_DIFFERENCE_POINTS:
                issues.append(
                    CheckIssue(
                        issue="pct_difference",
                        entity=entity,
                        fields=["application.owners.ownership_pct", f"{other_prefix}.ownership_pct"],
                        side_a=app_side,
                        side_b=other_side,
                        data={
                            "application_pct": app_pct,
                            "other_pct": other_pct,
                            "difference_points": difference,
                            "other_doc_id": other_doc,
                        },
                        message=f"Owner O{i + 1} holds {app_pct:g}% on the application and {other_pct:g}% on "
                        f"{other_doc} ({difference:g} points apart; more than {OWNERSHIP_DIFFERENCE_POINTS:g}).",
                    )
                )
    for i in app_only:
        pct = pct_of(application_owners[i])
        if pct is not None and pct < min_pct_listed:
            continue
        issues.append(
            CheckIssue(
                issue="missing_from_other",
                entity=f"owner:O{i + 1}",
                fields=["application.owners.name"],
                side_a=owner_evidence(application_owners[i], "application.owners", i + 1, application_doc),
                side_b=[],
                data={"application_pct": pct, "other_doc_id": other_doc},
                message=f"Owner O{i + 1} on the application is not listed on {other_doc}.",
            )
        )
    for j in other_only:
        issues.append(
            CheckIssue(
                issue="missing_from_application",
                entity=f"owner:{other_doc}-O{j + 1}",
                fields=[f"{other_prefix}.name"],
                side_a=[],
                side_b=owner_evidence(other_owners[j], other_prefix, j + 1, other_doc),
                data={"other_pct": pct_of(other_owners[j]), "other_doc_id": other_doc},
                message=f"Owner O{j + 1} on {other_doc} is missing from the application.",
            )
        )
    app_controls, other_controls = control_names(application_owners), control_names(other_owners)
    if app_controls and other_controls and app_controls != other_controls:
        issues.append(
            CheckIssue(
                issue="control_person_difference",
                entity="business",
                fields=["application.owners.is_control_person", f"{other_prefix}.is_control_person"],
                side_a=control_evidence(application_owners, "application.owners", application_doc),
                side_b=control_evidence(other_owners, other_prefix, other_doc),
                data={"other_doc_id": other_doc},
                message=f"The application and {other_doc} name a different control person.",
            )
        )
    return issues


def application_total_problem(application_owners: list[ExtractedOwner]) -> tuple[float | None, str | None]:
    """(total of the application's percentages, a message when it is not 100% within the tolerance).

    The total is None when an owner's percentage was not found (the rule cannot be applied).
    """
    percentages = [pct_of(owner) for owner in application_owners]
    if not percentages or any(pct is None for pct in percentages):
        return None, None
    total = round(sum(pct for pct in percentages if pct is not None), 1)
    if abs(total - 100.0) <= OWNERSHIP_TOTAL_TOLERANCE_POINTS:
        return total, None
    return total, f"The application's ownership percentages add up to {total:g}%, not 100% (NBC-6.1)."


def owner_consistency(
    refs: list[str],
    application_owners: list[ExtractedOwner],
    application_doc: str | None,
    other_owners: list[ExtractedOwner] | None,
    other_doc: str | None,
    other_prefix: str,
    min_pct_listed: float,
    total_must_be_100: bool = False,
) -> CheckResult:
    """owner_consistency for either line (module docstring).

    total_must_be_100 adds the NBC-6.1 rule that the application's percentages add up to 100%. A wrong total
    fails the check without a CheckIssue: it is a problem inside one document, not a disagreement between two,
    so it creates no contradiction. Without owners on both documents (and no wrong total) the check is not
    evaluated.
    """
    check_id = "owner_consistency"
    values: dict = {"application_owner_count": len(application_owners), "other_doc_id": other_doc}
    total, total_problem = application_total_problem(application_owners)
    if total is not None:
        values["application_total_pct"] = total
    if not total_must_be_100:
        total_problem = None
    application_evidence = []
    for i, owner in enumerate(application_owners):
        application_evidence.extend(owner_evidence(owner, "application.owners", i + 1, application_doc))
    if not application_owners or not other_owners:
        if total_problem is not None:
            return make_result(check_id, refs, "fail", total_problem, values, application_evidence)
        message = "Owners were not found on both the application and the other ownership document."
        return make_result(check_id, refs, "not_evaluated", message, values, application_evidence)
    values["other_owner_count"] = len(other_owners)
    evidence = list(application_evidence)
    for j, owner in enumerate(other_owners):
        evidence.extend(owner_evidence(owner, other_prefix, j + 1, other_doc))
    issues = owner_issues(application_owners, application_doc, other_owners, other_doc, other_prefix, min_pct_listed)
    values["issue_codes"] = [issue.issue for issue in issues]
    problems = [issue.message for issue in issues]
    if total_problem is not None:
        problems.append(total_problem)
    if not problems:
        message = f"The application's {len(application_owners)} owner(s) match {other_doc}."
        return make_result(check_id, refs, "pass", message, values, evidence)
    message = "Ownership differs: " + " ".join(problems)
    return make_result(check_id, refs, "fail", message, values, evidence, issues)
