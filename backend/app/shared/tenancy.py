"""
Tenant isolation enforcement.

Every protected route depends on `get_current_context`, which decodes the
JWT and returns the caller's identity. Every query in every module MUST
filter by `context.business_id`. This module doesn't magically enforce that
at the DB layer (that's a future Postgres RLS hardening step) — it just
makes the business_id impossible to forget: it's always right there in the
dependency every route already needs for auth.

Rule of thumb for every new query you write in this codebase:
    .filter(Model.business_id == context.business_id)
must appear, or the query is a tenant-isolation bug.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.security import JWTError, decode_access_token

from app.core.config import settings

# oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/token")

@dataclass(frozen=True)
class RequestContext:
    user_id: int
    business_id: int
    role: str


def get_current_context(token: str = Depends(oauth2_scheme)) -> RequestContext:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id = int(payload.get("sub"))
        business_id = payload.get("business_id")
        role = payload.get("role")
        if user_id is None or business_id is None:
            raise credentials_exception
    except (JWTError, ValueError, TypeError):
        raise credentials_exception

    return RequestContext(user_id=user_id, business_id=business_id, role=role)


def require_role(*allowed_roles: str):
    """Usage: Depends(require_role('owner'))"""

    def _check(context: RequestContext = Depends(get_current_context)) -> RequestContext:
        if context.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return context

    return _check
