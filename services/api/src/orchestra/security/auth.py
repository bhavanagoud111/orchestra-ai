"""Bearer-token authentication with role-based authorization.

Tokens are configured through the environment and compared in constant time.
The web app holds its token server-side (Next.js route handler) and never sends
it to the browser.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from enum import IntEnum

from fastapi import Depends, HTTPException, Request, status

from orchestra.config import Settings


class Role(IntEnum):
    VIEWER = 10
    OPERATOR = 20
    APPROVER = 30
    ADMIN = 40


@dataclass(frozen=True)
class Principal:
    name: str
    role: Role

    def has(self, role: Role) -> bool:
        return self.role >= role


def _token_table(settings: Settings) -> list[tuple[str, Role]]:
    table: list[tuple[str, Role]] = []
    for attr, role in (
        ("admin_token", Role.ADMIN),
        ("approver_token", Role.APPROVER),
        ("operator_token", Role.OPERATOR),
        ("viewer_token", Role.VIEWER),
    ):
        secret = getattr(settings, attr)
        if secret is not None and secret.get_secret_value():
            table.append((secret.get_secret_value(), role))
    return table


def authenticate(token: str, settings: Settings) -> Principal | None:
    for candidate, role in _token_table(settings):
        if hmac.compare_digest(candidate.encode(), token.encode()):
            return Principal(name=role.name.lower(), role=role)
    return None


def get_principal(request: Request) -> Principal:
    settings: Settings = request.app.state.services.settings
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    principal = authenticate(token.strip(), settings)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token", headers={"WWW-Authenticate": "Bearer"})
    request.state.principal = principal
    return principal


def require(role: Role):
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {role.name.lower()} role")
        return principal

    return dependency
