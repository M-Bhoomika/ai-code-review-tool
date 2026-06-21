from enum import Enum

import strawberry


@strawberry.enum
class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@strawberry.enum
class Category(Enum):
    BUGS = "bugs"
    SECURITY = "security"
    PERFORMANCE = "performance"
    LOGIC = "logic"
    MAINTAINABILITY = "maintainability"
    CODE_QUALITY = "code_quality"
    OTHER = "other"


def parse_severity(value: str) -> Severity:
    normalized = (value or "info").strip().lower()
    for severity in Severity:
        if severity.value == normalized:
            return severity
    return Severity.INFO


def parse_category(value: str) -> Category:
    normalized = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "bugs": Category.BUGS,
        "bug": Category.BUGS,
        "security": Category.SECURITY,
        "security_issues": Category.SECURITY,
        "performance": Category.PERFORMANCE,
        "performance_issues": Category.PERFORMANCE,
        "logic": Category.LOGIC,
        "maintainability": Category.MAINTAINABILITY,
        "maintainability_issues": Category.MAINTAINABILITY,
        "code_quality": Category.CODE_QUALITY,
        "code_quality_issues": Category.CODE_QUALITY,
    }
    return aliases.get(normalized, Category.OTHER)
