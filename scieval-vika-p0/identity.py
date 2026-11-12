"""Identity/authorization boundary for local approval and publication."""
from typing import Protocol


class AuthenticatedReviewerProvider(Protocol):
    """Trusted adapter: authenticate credential and authorize action on job_id.

    Return a stable nonblank principal identifier only after both checks pass;
    return None or raise PermissionError to deny. Never trust an unverified
    caller-supplied identity. Actions are 'review' and 'publish'. Production
    adapters must validate credentials, permissions, and their scope themselves.
    """
    def authorize(self, credential: object, action: str, job_id: str) -> str | None: ...


class SyntheticLocalReviewerProvider:
    """Insecure fixed tokens for local demonstrations only; no authentication."""
    def authorize(self, credential: object, action: str, job_id: str) -> str | None:
        grants = {'review': {'demo-review-token': 'synthetic-reviewer'},
                  'publish': {'demo-publish-token': 'synthetic-publisher'}}
        if not isinstance(credential, str):
            return None
        return grants.get(action, {}).get(credential)
