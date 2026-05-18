from enum import StrEnum


class ClarificationType(StrEnum):
    MISSING_INFO = "missing_info"
    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    CANDIDATE_SELECTION = "candidate_selection"
    RISK_CONFIRMATION = "risk_confirmation"
    SUGGESTION = "suggestion"


def coerce_clarification_type(value: str | ClarificationType | None) -> ClarificationType:
    if isinstance(value, ClarificationType):
        return value
    if value is None:
        return ClarificationType.MISSING_INFO
    return ClarificationType(value)
