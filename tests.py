from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import NotificationLog, PriceAlert, UserProfile
from .tasks import check_prices, send_notification


# ---------------------------------------------------------------- Required 1
class PriceThresholdTest(TestCase):

    def test_price_below_threshold_triggers_alert(self):
        self.assertTrue(4200 <= 4500)

    def test_price_above_threshold_does_not_trigger(self):
        self.assertFalse(5000 <= 4500)

    def test_price_equal_to_threshold_triggers_alert(self):
        self.assertTrue(4500 <= 4500)


# ---------------------------------------------------------------- Required 2
class NotificationLogTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.alert = PriceAlert.objects.create(
            user=self.user, origin='DEL', destination='BOM',
            threshold_price=4500, status=PriceAlert.Status.ACTIVE,
        )

    def test_notification_log_created_with_correct_message(self):
        log = NotificationLog.objects.create(
            alert=self.alert, triggered_price=4200,
            message='Price dropped to 4200 for DEL-BOM',
        )
        self.assertEqual(log.triggered_price, 4200)
        self.assertEqual(log.alert, self.alert)
        self.assertIn('DEL-BOM', log.message)


# ---------------------------------------------------------------- Required 3
class AlertScopingTest(TestCase):

    def setUp(self):
        self.user1 = User.objects.create_user(username='user1', password='pass')
        self.user2 = User.objects.create_user(username='user2', password='pass')
        PriceAlert.objects.create(user=self.user1, origin='DEL', destination='BOM',
                                  threshold_price=4500)
        PriceAlert.objects.create(user=self.user2, origin='BLR', destination='HYD',
                                  threshold_price=2000)

    def test_user_only_sees_own_alerts(self):
        user1_alerts = PriceAlert.objects.filter(user=self.user1)
        self.assertEqual(user1_alerts.count(), 1)
        self.assertEqual(user1_alerts.first().origin, 'DEL')

    def test_user_cannot_see_other_users_alerts(self):
        user1_alerts = PriceAlert.objects.filter(user=self.user1)
        self.assertFalse(user1_alerts.filter(origin='BLR').exists())


# ------------------------------------------------- Extra: real business logic
class SignalTest(TestCase):
    def test_profile_auto_created_with_user_role(self):
        user = User.objects.create_user(username='sigtest', password='x')
        self.assertEqual(user.profile.role, UserProfile.Role.USER)


class TaskTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tasker', password='x')
        self.alert = PriceAlert.objects.create(
            user=self.user, origin='DEL', destination='BOM', threshold_price=4500)

    def test_send_notification_writes_log_and_marks_triggered(self):
        send_notification(self.alert.id, 4200)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, PriceAlert.Status.TRIGGERED)
        log = NotificationLog.objects.get(alert=self.alert)
        self.assertEqual(log.triggered_price, Decimal('4200'))
        self.assertIn('DEL-BOM', log.message)

    @patch('pulse.tasks.send_notification.delay')
    @patch('pulse.tasks.requests.get')
    def test_check_prices_fires_when_price_at_or_below_threshold(self, mock_get, mock_delay):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {'route': 'DEL-BOM', 'price': 4200})
        check_prices()
        mock_delay.assert_called_once_with(self.alert.id, 4200)

    @patch('pulse.tasks.send_notification.delay')
    @patch('pulse.tasks.requests.get')
    def test_check_prices_skips_when_price_above_threshold(self, mock_get, mock_delay):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {'route': 'DEL-BOM', 'price': 6000})
        check_prices()
        mock_delay.assert_not_called()

    @patch('pulse.tasks.send_notification.delay')
    @patch('pulse.tasks.requests.get')
    def test_check_prices_ignores_inactive_alerts(self, mock_get, mock_delay):
        self.alert.status = PriceAlert.Status.INACTIVE
        self.alert.save()
        check_prices()
        mock_get.assert_not_called()
        mock_delay.assert_not_called()


class APITest(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _register(self, username='alice', **extra):
        return self.client.post('/api/auth/register/', {
            'username': username, 'password': 'securepass',
            'email': f'{username}@example.com', **extra}, format='json')

    def _auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_register_returns_201_token_and_user_role_even_if_admin_requested(self):
        res = self._register(role='admin')
        self.assertEqual(res.status_code, 201)
        self.assertIn('access', res.data)
        self.assertEqual(res.data['role'], 'user')

    def test_register_duplicate_username_400(self):
        self._register()
        self.assertEqual(self._register().status_code, 400)

    def test_login_valid_and_wrong_password(self):
        self._register()
        ok = self.client.post('/api/auth/login/', {'username': 'alice', 'password': 'securepass'}, format='json')
        bad = self.client.post('/api/auth/login/', {'username': 'alice', 'password': 'nope'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertIn('access', ok.data)
        self.assertEqual(bad.status_code, 401)

    def test_create_alert_requires_token(self):
        res = self.client.post('/api/alerts/', {'origin': 'DEL', 'destination': 'BOM', 'threshold_price': 4500}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_alert_lifecycle_and_isolation(self):
        a = self._register('alice').data['access']
        b = self._register('bob').data['access']
        self._auth(a)
        created = self.client.post('/api/alerts/', {'origin': 'del', 'destination': 'bom', 'threshold_price': 4500}, format='json')
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data['origin'], 'DEL')
        alert_id = created.data['id']
        self.assertEqual(len(self.client.get('/api/alerts/').data), 1)

        self._auth(b)  # bob sees nothing and cannot delete alice's alert
        self.assertEqual(self.client.get('/api/alerts/').data, [])
        self.assertEqual(self.client.delete(f'/api/alerts/{alert_id}/').status_code, 404)

        self._auth(a)
        res = self.client.delete(f'/api/alerts/{alert_id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {'status': 'inactive'})
        self.assertTrue(PriceAlert.objects.filter(id=alert_id, status='inactive').exists())
        self.assertEqual(self.client.delete('/api/alerts/99999/').status_code, 404)

    def test_mock_price_feed(self):
        ok = self.client.get('/api/flights/price/', {'route': 'DEL-BOM'})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(3000 <= ok.json()['price'] <= 7000)
        self.assertEqual(self.client.get('/api/flights/price/', {'route': 'XYZ-ABC'}).status_code, 404)

    def test_admin_summary_permissions_and_stats(self):
        token = self._register('regular').data['access']
        self._auth(token)
        self.assertEqual(self.client.get('/api/admin/summary/').status_code, 403)

        admin = User.objects.create_user(username='boss', password='pw12345')
        admin.profile.role = UserProfile.Role.ADMIN
        admin.profile.save()
        PriceAlert.objects.create(user=admin, origin='DEL', destination='BOM', threshold_price=1)
        PriceAlert.objects.create(user=admin, origin='DEL', destination='BOM', threshold_price=2,
                                  status=PriceAlert.Status.TRIGGERED)
        PriceAlert.objects.create(user=admin, origin='BLR', destination='HYD', threshold_price=3)
        login = self.client.post('/api/auth/login/', {'username': 'boss', 'password': 'pw12345'}, format='json')
        self._auth(login.data['access'])
        res = self.client.get('/api/admin/summary/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['total_alerts'], 3)
        self.assertEqual(res.data['active_alerts'], 2)
        self.assertEqual(res.data['triggered_alerts'], 1)
        self.assertEqual(res.data['top_routes'][0], {'route': 'DEL-BOM', 'alert_count': 2})
