"""Typed errors shared by the YARA-X backend and HTTP API."""


class YaraXError(Exception):
    code = "yarax_error"


class ValidationError(YaraXError):
    code = "validation_error"

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


class ConflictError(YaraXError):
    code = "revision_conflict"


class NotFoundError(YaraXError):
    code = "not_found"


class ScanTimeoutError(YaraXError):
    code = "scan_timeout"


class ScanCancelledError(YaraXError):
    code = "scan_cancelled"


class ScanFailedError(YaraXError):
    code = "scan_failed"


class RuleCompileError(YaraXError):
    code = "compile_error"
