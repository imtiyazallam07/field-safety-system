"""
Webhook Dispatcher for External Platform Integration
Allows external third-party systems (cloud dashboards, Slack/Discord, MQTT bridges,
monitoring stations, IoT hubs) to register webhooks and receive live push notifications.
"""

import time
import json
import hmac
import hashlib
import threading
import requests
from datetime import datetime
from typing import Dict, List, Optional, Any
from .models import WebhookSubscriptionIn, WebhookSubscription


class WebhookManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        self.subscription_counter: int = 0

    def subscribe(self, sub_in: WebhookSubscriptionIn) -> Dict[str, Any]:
        with self.lock:
            self.subscription_counter += 1
            sub_id = f"wh_{self.subscription_counter:04d}_{int(time.time())}"
            sub_data = {
                "id": sub_id,
                "url": sub_in.url,
                "events": sub_in.events,
                "secret": sub_in.secret,
                "description": sub_in.description or f"Platform Webhook {sub_id}",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_delivered_at": None,
                "total_deliveries": 0,
                "failed_deliveries": 0
            }
            self.subscriptions[sub_id] = sub_data
            return sub_data

    def unsubscribe(self, sub_id: str) -> bool:
        with self.lock:
            if sub_id in self.subscriptions:
                del self.subscriptions[sub_id]
                return True
            return False

    def list_subscriptions(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.subscriptions.values())

    def get_subscription(self, sub_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.subscriptions.get(sub_id)

    def dispatch_event(self, event_type: str, payload: Any):
        """Asynchronously dispatches an event to all subscribed external platforms."""
        threading.Thread(target=self._do_dispatch, args=(event_type, payload), daemon=True).start()

    def _do_dispatch(self, event_type: str, payload: Any):
        with self.lock:
            active_subs = list(self.subscriptions.values())

        if not active_subs:
            return

        envelope = {
            "event": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": payload
        }
        body_json = json.dumps(envelope)

        for sub in active_subs:
            events = sub.get("events", [])
            # Check if subscribed to wildcard or specific event type
            if "*" not in events and event_type not in events:
                # Check category prefixes e.g. "alert.*"
                prefix = event_type.split('.')[0] + ".*"
                if prefix not in events:
                    continue

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "FieldSafetySystem-Webhook/1.0",
                "X-FieldSafety-Event": event_type,
                "X-FieldSafety-Timestamp": envelope["timestamp"]
            }

            # Optional HMAC SHA256 Signature for security
            secret = sub.get("secret")
            if secret:
                signature = hmac.new(secret.encode('utf-8'), body_json.encode('utf-8'), hashlib.sha256).hexdigest()
                headers["X-FieldSafety-Signature"] = f"sha256={signature}"

            url = sub["url"]
            try:
                resp = requests.post(url, data=body_json, headers=headers, timeout=4.0)
                with self.lock:
                    if sub["id"] in self.subscriptions:
                        self.subscriptions[sub["id"]]["last_delivered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.subscriptions[sub["id"]]["total_deliveries"] += 1
            except Exception as e:
                print(f"[Webhook] Failed to deliver to {url}: {e}")
                with self.lock:
                    if sub["id"] in self.subscriptions:
                        self.subscriptions[sub["id"]]["failed_deliveries"] += 1


webhook_manager = WebhookManager()
