from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ChannelProduct, Product
from app.schemas.product import ChannelProductMappingCreate, ProductCreate, ProductResponse
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[ProductResponse])
def list_products(
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    # Every list/get query in the app follows this shape: filter by
    # business_id first, always. See app/shared/tenancy.py for why.
    return db.query(Product).filter(Product.business_id == context.business_id).all()


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    product = Product(business_id=context.business_id, **payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: int,
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.business_id == context.business_id,
    ).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


@router.post("/mapping", status_code=status.HTTP_201_CREATED)
def create_channel_mapping(
    payload: ChannelProductMappingCreate,
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    """Maps a channel-specific SKU/product to a master Product.
    See Section 18 of the master prompt — exact / manual / alias mapping."""
    product = db.query(Product).filter(
        Product.id == payload.product_id,
        Product.business_id == context.business_id,
    ).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    mapping = ChannelProduct(business_id=context.business_id, **payload.model_dump())
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return {"id": mapping.id, "status": "mapped"}
