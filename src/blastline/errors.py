"""Errors that must be visible to callers instead of silently degraded."""


class BlastlineError(Exception):
    """Base class for expected Blastline failures."""


class ConfigurationError(BlastlineError):
    """The runtime configuration is incomplete or invalid."""


class ExternalCallError(BlastlineError):
    """A live external request failed after the configured retry policy."""


class ParseError(BlastlineError):
    """A source record could not be parsed into the required model."""


class Abstention(BlastlineError):
    """Blastline cannot safely answer a query from the available evidence."""
