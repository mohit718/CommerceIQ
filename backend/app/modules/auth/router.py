from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import User
from app.modules.auth import service
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse, UserResponse
from app.shared.exceptions import DuplicateEntityError, ValidationFailedError
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    try:
        _, token = service.signup(db, payload)
    except DuplicateEntityError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    try:
        _, token = service.login(db, payload)
    except ValidationFailedError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    return TokenResponse(access_token=token)


@router.post("/token", response_model=TokenResponse, include_in_schema=False)
def login_for_swagger(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """OAuth2-form-compatible login, used only by Swagger UI's 'Authorize'
    button (which always POSTs application/x-www-form-urlencoded with a
    `username` field, per the OAuth2 spec — not our JSON `email` field).
    Real clients should use POST /auth/login instead. client_id/client_secret
    from the OAuth2 form are unused — we don't do client registration."""
    try:
        _, token = service.login(
            db, LoginRequest(email=form_data.username, password=form_data.password)
        )
    except ValidationFailedError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
def me(
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(
        User.id == context.user_id,
        User.business_id == context.business_id,  # tenancy filter, even on self-lookup
    ).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user
