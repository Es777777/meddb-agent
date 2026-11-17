class MedDBError(Exception):
    """Base error shown to CLI users without a Python traceback."""


class ConfigError(MedDBError):
    """Configuration is missing or invalid."""


class SecurityError(MedDBError):
    """A query violates the read-only security policy."""


class CatalogError(MedDBError):
    """The local schema catalog is unavailable or invalid."""


class TemplateError(MedDBError):
    """A verified template or one of its parameters is invalid."""


class QueryError(MedDBError):
    """PostgreSQL rejected or could not complete a query."""

