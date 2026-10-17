import requests
from celery import shared_task
from django.conf import settings

from .models import NotificationLog, PriceAlert


@shared_task
def check_prices():
    """Runs every 60s via Celery Beat. Fires send_notification when a threshold is hit."""
    active_alerts = PriceAlert.objects.filter(status=PriceAlert.Status.ACTIVE)
    routes = active_alerts.values_list('origin', 'destination').distinct()

    for origin, destination in routes:
        route = f'{origin}-{destination}'

        # Backend-to-backend call to our own mock price endpoint
        try:
            response = requests.get(
                settings.PRICE_FEED_URL, params={'route': route}, timeout=5
            )
        except requests.RequestException:
            continue
        if response.status_code != 200:
            continue
        current_price = response.json().get('price')
        if current_price is None:
            continue

        route_alerts = active_alerts.filter(origin=origin, destination=destination)
        for alert in route_alerts:
            if current_price <= float(alert.threshold_price):
                # .delay() -> async; check_prices never waits for the notification
                send_notification.delay(alert.id, current_price)


@shared_task
def send_notification(alert_id, triggered_price):
    alert = PriceAlert.objects.get(id=alert_id)
    message = (
        f'Price alert triggered! {alert.origin}-{alert.destination} '
        f'is now ₹{triggered_price} - below your threshold of '
        f'₹{alert.threshold_price}'
    )
    NotificationLog.objects.create(
        alert=alert, triggered_price=triggered_price, message=message
    )
    # Mark as triggered so it doesn't fire again
    alert.status = PriceAlert.Status.TRIGGERED
    alert.save(update_fields=['status'])
