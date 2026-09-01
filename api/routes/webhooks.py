"""
Webhook Integration Endpoints for External Platforms
Allows external dashboards, incident response platforms (PagerDuty, Slack, Discord),
and IoT cloud platforms to register webhooks for event delivery.
"""

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any

from ..webhooks import webhook_manager
from ..models import WebhookSubscriptionIn, WebhookSubscription

router = APIRouter(tags=["External Platform Webhooks"])


@router.get(
    "/api/v1/webhooks",
    response_model=List[WebhookSubscription],
    summary="List Registered External Webhooks",
    description="Returns all active webhook endpoints registered by external platforms."
)
async def list_webhooks():
    return webhook_manager.list_subscriptions()


@router.post(
    "/api/v1/webhooks",
    response_model=WebhookSubscription,
    summary="Register External Webhook Subscription",
    description="Registers an external platform URL to receive real-time HTTP POST push notifications for safety alerts and sensor telemetry."
)
async def create_webhook(sub: WebhookSubscriptionIn):
    if not sub.url.startswith("http://") and not sub.url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Webhook URL must begin with http:// or https://")
    
    created = webhook_manager.subscribe(sub)
    return created


@router.get(
    "/api/v1/webhooks/{webhook_id}",
    response_model=WebhookSubscription,
    summary="Get Webhook Details",
    description="Retrieves status and delivery metrics for a specific webhook."
)
async def get_webhook(webhook_id: str):
    sub = webhook_manager.get_subscription(webhook_id)
    if not sub:
        raise HTTPException(status_code=404, detail=f"Webhook '{webhook_id}' not found")
    return sub


@router.delete(
    "/api/v1/webhooks/{webhook_id}",
    summary="Unsubscribe Webhook",
    description="Removes a webhook subscription."
)
async def delete_webhook(webhook_id: str):
    removed = webhook_manager.unsubscribe(webhook_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Webhook '{webhook_id}' not found")
    return {"status": "unsubscribed", "webhook_id": webhook_id}


@router.post(
    "/api/v1/webhooks/{webhook_id}/test",
    summary="Test Webhook Delivery",
    description="Sends a synthetic test safety alert payload to verify connectivity with the external platform."
)
async def test_webhook(webhook_id: str):
    sub = webhook_manager.get_subscription(webhook_id)
    if not sub:
        raise HTTPException(status_code=404, detail=f"Webhook '{webhook_id}' not found")

    test_payload = {
        "event": "alert.test",
        "message": "Field Safety System test ping to external platform",
        "timestamp": "2026-09-01T12:00:00Z"
    }
    webhook_manager.dispatch_event("alert.test", test_payload)
    return {"status": "test_dispatched", "target_url": sub["url"]}
