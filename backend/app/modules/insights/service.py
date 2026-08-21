"""
Read-side of the insight engine. Never writes anything — insight creation/
update/resolution is entirely owned by app/jobs/insight_jobs.py's
generate_insights_for_business(). This module only queries and shapes
already-persisted product_insights rows for the API.
"""
from sqlalchemy.orm import Session

from app.models import Product, ProductInsight

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def list_insights(
    db: Session,
    business_id: int,
    status: str = "open",
    type: str | None = None,
    severity: str | None = None,
    product_id: int | None = None,
) -> list[dict]:
    """status: 'open' (default, resolved_at IS NULL), 'resolved', or 'all'.
    Sorted by severity (critical first), then most-recently-created first
    within the same severity."""
    q = (
        db.query(ProductInsight, Product)
        .join(Product, ProductInsight.product_id == Product.id)
        .filter(ProductInsight.business_id == business_id)
    )

    if status == "open":
        q = q.filter(ProductInsight.resolved_at.is_(None))
    elif status == "resolved":
        q = q.filter(ProductInsight.resolved_at.is_not(None))
    # status == "all" -> no additional filter

    if type:
        q = q.filter(ProductInsight.type == type)
    if severity:
        q = q.filter(ProductInsight.severity == severity)
    if product_id:
        q = q.filter(ProductInsight.product_id == product_id)

    rows = q.all()
    rows.sort(
        key=lambda pair: (
            _SEVERITY_ORDER.get(pair[0].severity, 99),
            -(pair[0].created_at.timestamp() if pair[0].created_at else 0),
        )
    )

    return [
        {
            "id": insight.id,
            "product_id": insight.product_id,
            "sku": product.sku,
            "product_name": product.name,
            "type": insight.type,
            "severity": insight.severity,
            "confidence": insight.confidence,
            "message": insight.message,
            "recommendation": insight.recommendation,
            "created_at": insight.created_at,
            "resolved_at": insight.resolved_at,
        }
        for insight, product in rows
    ]
