"""
Resolves a channel's external_sku to our master Product — the practical
implementation of Section 18's mapping progression (exact -> manual ->
alias -> future intelligent matching). This module only ever handles the
'exact' tier automatically; anything it can't resolve is left for manual
mapping via the product_mapping endpoint, never guessed.
"""
from sqlalchemy.orm import Session

from app.ingestion.normalization.normalizers import normalize_sku
from app.models import ChannelProduct, Product


def resolve_product(
    db: Session, business_id: int, channel_id: int, external_sku: str | None
) -> tuple[Product | None, ChannelProduct | None]:
    """Returns (product, channel_product) if resolved, else (None, None).
    Never raises — callers decide how to handle an unmapped row."""
    normalized = normalize_sku(external_sku)
    if not normalized:
        return None, None

    # 1. Already mapped for this channel? (fast path — most rows after the
    #    first import of a given catalog hit this.)
    existing_mapping = (
        db.query(ChannelProduct)
        .filter(
            ChannelProduct.business_id == business_id,
            ChannelProduct.channel_id == channel_id,
            ChannelProduct.external_sku == external_sku,
        )
        .first()
    )
    if existing_mapping:
        return existing_mapping.product, existing_mapping

    # 2. No mapping yet, but does a master Product with this exact SKU
    #    already exist? If so, auto-create the mapping — this is the
    #    'exact' tier from Section 18, safe to do without a human.
    product = (
        db.query(Product)
        .filter(Product.business_id == business_id, Product.sku == normalized)
        .first()
    )
    if product:
        new_mapping = ChannelProduct(
            business_id=business_id,
            product_id=product.id,
            channel_id=channel_id,
            external_sku=external_sku,
            mapping_method="exact",
        )
        db.add(new_mapping)
        db.flush()  # get new_mapping.id without committing the whole batch
        return product, new_mapping

    # 3. Genuinely unmapped — queued for manual mapping, not guessed.
    return None, None
