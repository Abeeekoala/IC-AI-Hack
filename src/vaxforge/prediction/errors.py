"""Exception types shared by the S3-S7 prediction core."""
from __future__ import annotations


class VaxForgeError(Exception):
    """Base class for every error raised deliberately by this package."""


class ContractError(VaxForgeError):
    """An upstream artifact does not match the contract in docs/CONTRACT_S3_S7.md."""


class ConfigError(VaxForgeError):
    """Configuration is invalid or violates a project rule (e.g. BepiPred-2.0, AF3 weights)."""


class ToolUnavailable(VaxForgeError):
    """An external tool is not installed / not configured.

    Stages catch this and record the gate or sub-step as ``unavailable``. They never
    substitute a different tool silently and never treat "could not run" as "passed".
    """


class ToolFailed(VaxForgeError):
    """An external tool ran but failed (non-zero exit, missing output, parse failure)."""
