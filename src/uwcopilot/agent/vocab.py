"""Shared words of the agent: recommendations, reviewer roles and actions, and who a file can be referred to.

Kept in one small module so the application schema, the reference policy, the human-review step and the
evaluation all use exactly the same spelling.
"""

from typing import Literal

# What the reference policy (and the memo) may recommend. None of these is a decision: a human decides.
Recommendation = Literal["approve", "refer", "decline_for_review", "request_more_information"]
RECOMMENDATIONS: tuple[str, ...] = ("approve", "refer", "decline_for_review", "request_more_information")

# What a human reviewer can do at the approval checkpoint.
ReviewerAction = Literal["approve", "decline", "refer", "request_more_information"]
REVIEWER_ACTIONS: tuple[str, ...] = ("approve", "decline", "refer", "request_more_information")

# The action that agrees with each recommendation. Any other action is an override (NBC-8.2, HM-10.2).
MATCHING_ACTION: dict[str, str] = {
    "approve": "approve",
    "refer": "refer",
    "decline_for_review": "decline",
    "request_more_information": "request_more_information",
}

# Reviewer roles at the checkpoint, lowest authority first.
ReviewerRole = Literal["underwriter", "senior_underwriter"]
REVIEWER_ROLES: tuple[str, ...] = ("underwriter", "senior_underwriter")

# Every access role (security/rbac.py): the two reviewer roles plus two that may never decide a file. A
# model_validator promotes models and signs migration certificates; an admin manages permissions and reads audit
# logs. Roles are CLAIMED in this demo (no identity provider for the CLI or the agent).
AccessRole = Literal["underwriter", "senior_underwriter", "model_validator", "admin"]
ACCESS_ROLES: tuple[str, ...] = ("underwriter", "senior_underwriter", "model_validator", "admin")

# Business lines and the guideline document each one follows. startup_credit follows the SYNTHETIC startup program
# in corpus/programs/ (plus the Northwind sections it keeps); that document is not in the RAG index.
Line = Literal["credit", "insurance", "startup_credit"]
GUIDELINE_DOC_FOR_LINE: dict[str, str] = {
    "credit": "northwind_business_credit",
    "insurance": "harborline_mutual_commercial",
    "startup_credit": "northwind_startup_program",
}

# Functions a file can be referred to, in the order a review queue is chosen (most urgent first).
COMPLIANCE = "Compliance"
FRAUD_OPERATIONS = "Fraud Operations"
SPECIAL_INVESTIGATIONS = "Special Investigations Unit"
KYC_OPERATIONS = "KYC Operations"
CATASTROPHE_MANAGEMENT = "Catastrophe Management"
CHIEF_CREDIT_OFFICER = "Chief Credit Officer"
CHIEF_UNDERWRITING_OFFICER = "Chief Underwriting Officer"
CREDIT_COMMITTEE = "Credit Committee"
UNDERWRITING_MANAGER = "Underwriting Manager"
SENIOR_UNDERWRITER = "Senior Underwriter"
UNDERWRITER = "Underwriter"
REFERRAL_PRIORITY: tuple[str, ...] = (
    COMPLIANCE,
    FRAUD_OPERATIONS,
    SPECIAL_INVESTIGATIONS,
    KYC_OPERATIONS,
    CATASTROPHE_MANAGEMENT,
    CREDIT_COMMITTEE,
    CHIEF_CREDIT_OFFICER,
    CHIEF_UNDERWRITING_OFFICER,
    UNDERWRITING_MANAGER,
    SENIOR_UNDERWRITER,
    UNDERWRITER,
)
# Functions whose referral blocks approval until they resolve it in writing (NBC-7.3, HM-9.3).
BLOCKING_FUNCTIONS: frozenset[str] = frozenset({COMPLIANCE, FRAUD_OPERATIONS, SPECIAL_INVESTIGATIONS, KYC_OPERATIONS})

SYNTHETIC_LABEL = "SYNTHETIC"
FAKE_LLM_STAMP = "FAKE LLM — test mode"
