from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.models import Business, User
from app.schemas.auth import LoginRequest, SignupRequest
from app.shared.exceptions import DuplicateEntityError, ValidationFailedError


def signup(db: Session, payload: SignupRequest) -> tuple[User, str]:
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise DuplicateEntityError("User", f"email={payload.email} is already registered")

    # First user of a new business is always the owner.
    business = Business(name=payload.business_name)
    db.add(business)
    db.flush()  # get business.id before creating the user

    user = User(
        business_id=business.id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role="owner",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user_id=user.id, business_id=user.business_id, role=user.role)
    return user, token


def login(db: Session, payload: LoginRequest) -> tuple[User, str]:
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise ValidationFailedError("Invalid email or password")

    token = create_access_token(user_id=user.id, business_id=user.business_id, role=user.role)
    return user, token
