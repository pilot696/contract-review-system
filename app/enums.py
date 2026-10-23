"""全局枚举：单一来源，DB 层用 VARCHAR + CHECK 落库（见 models.py）。"""

from enum import StrEnum


class TaskStatus(StrEnum):
    """任务状态：pending → parsing → reviewing → done，任一步失败 → blocked。"""

    PENDING = "pending"
    PARSING = "parsing"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    DONE = "done"


class WriteStatus(StrEnum):
    """评论回写状态。"""

    NOT_WRITTEN = "not_written"
    WRITING = "writing"
    SUCCESS = "success"
    FAILED = "failed"


class DownloadStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class ParseStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class RuleStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class MatchMode(StrEnum):
    """规则匹配模式（设计文档 §7.5.2）。"""

    KEYWORD = "keyword"
    REGEX = "regex"
    FIELD_ABSENT = "field_absent"
    NUMERIC_THRESHOLD = "numeric_threshold"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExtractStatus(StrEnum):
    EXTRACTED = "extracted"
    MISSING = "missing"
    UNREADABLE = "unreadable"


class HitStatus(StrEnum):
    HIT = "hit"
    SKIPPED = "skipped"


class FailedStage(StrEnum):
    PULL = "pull"
    DOWNLOAD = "download"
    PARSE = "parse"
    REVIEW = "review"
    WRITEBACK = "writeback"


class BlockedReason(StrEnum):
    ATTACHMENT_MISSING = "ATTACHMENT_MISSING"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    OCR_FAILED = "OCR_FAILED"
    DOCUMENT_EMPTY = "DOCUMENT_EMPTY"
    PARSE_FAILED = "PARSE_FAILED"
    APPROVAL_API_ERROR = "APPROVAL_API_ERROR"
    RULE_ENGINE_ERROR = "RULE_ENGINE_ERROR"
    WRITEBACK_FAILED = "WRITEBACK_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogType(StrEnum):
    PULL = "pull"
    DOWNLOAD = "download"
    PARSE = "parse"
    REVIEW = "review"
    WRITEBACK = "writeback"
    STATE = "state"
    API = "api"


# ---- DB CHECK 约束使用的取值快照（避免手写字符串漂移） ----
TASK_STATUS_VALUES = tuple(s.value for s in TaskStatus)
WRITE_STATUS_VALUES = tuple(s.value for s in WriteStatus)
DOWNLOAD_STATUS_VALUES = tuple(s.value for s in DownloadStatus)
PARSE_STATUS_VALUES = tuple(s.value for s in ParseStatus)
RULE_STATUS_VALUES = tuple(s.value for s in RuleStatus)
MATCH_MODE_VALUES = tuple(s.value for s in MatchMode)
RISK_LEVEL_VALUES = tuple(s.value for s in RiskLevel)
HIT_STATUS_VALUES = tuple(s.value for s in HitStatus)
FAILED_STAGE_VALUES = tuple(s.value for s in FailedStage)
BLOCKED_REASON_VALUES = tuple(s.value for s in BlockedReason)
LOG_LEVEL_VALUES = tuple(s.value for s in LogLevel)
LOG_TYPE_VALUES = tuple(s.value for s in LogType)
