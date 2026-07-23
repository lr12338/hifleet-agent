"""Shared Skills V2 errors with intentionally non-sensitive messages."""


class SkillsV2Error(RuntimeError):
    """Base error for recoverable Shared Skills V2 failures."""


class ManifestValidationError(SkillsV2Error):
    """A manifest is malformed or unsafe to enable."""


class PolicyViolationError(SkillsV2Error):
    """A profile attempted to use a tool outside its V2 policy."""


class UpstreamVersionError(SkillsV2Error):
    """The requested upstream revision is not verified or not available."""
