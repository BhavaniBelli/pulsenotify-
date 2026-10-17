import random

from django.contrib.auth import authenticate
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import NotificationLog, PriceAlert
from .permissions import IsAdminUser
from .serializers import LoginSerializer, PriceAlertSerializer, RegisterSerializer


def _tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


# ---------------------------------------------------------------- 5.1 Register
class RegisterView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)   # duplicate username -> 400
        user = serializer.save()
        tokens = _tokens_for(user)
        return Response(
            {
                'username': user.username,
                'access': tokens['access'],
                'refresh': tokens['refresh'],
                'role': user.profile.role,
            },
            status=status.HTTP_201_CREATED,
        )


# ------------------------------------------------------------------ 5.2 Login
class LoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            username=serializer.validated_data['username'],
            password=serializer.validated_data['password'],
        )
        if user is None:
            return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)
        tokens = _tokens_for(user)
        return Response(
            {
                'username': user.username,
                'access': tokens['access'],
                'refresh': tokens['refresh'],
                'role': user.profile.role,
            },
            status=status.HTTP_200_OK,
        )


# ------------------------------------------------- 5.3 / 5.4  POST + GET alerts
class AlertListCreateView(generics.ListCreateAPIView):
    serializer_class = PriceAlertSerializer

    def get_queryset(self):
        # Scoped to the logged-in user: nobody ever sees another user's alerts.
        return PriceAlert.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)  # user from token, never from body

    def list(self, request, *args, **kwargs):
        # Plain list (200 + [] when empty) - no pagination wrapper, never a 404.
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)


# ----------------------------------------------------------- 5.5 DELETE alert
class AlertDeactivateView(APIView):
    def delete(self, request, id):
        alert = get_object_or_404(PriceAlert, id=id)
        if alert.user != request.user:
            raise Http404  # never reveal that another user's alert exists
        alert.status = PriceAlert.Status.INACTIVE
        alert.save(update_fields=['status'])
        return Response({'status': 'inactive'}, status=status.HTTP_200_OK)


# ------------------------------------------------------- 5.6 Mock price feed
MOCK_PRICES = {
    'DEL-BOM': (3000, 7000),
    'BLR-HYD': (1500, 4000),
    'DEL-BLR': (4000, 9000),
    'BOM-GOA': (2000, 5000),
}


def get_flight_price(request):
    """Internal mock feed. No authentication - called by the Celery task."""
    route = request.GET.get('route', '')
    price_range = MOCK_PRICES.get(route)
    if not price_range:
        return JsonResponse({'error': 'Route not found'}, status=404)
    price = random.randint(*price_range)
    return JsonResponse({'route': route, 'price': price})


# ------------------------------------------------------ 5.7 Admin summary
class AdminSummaryView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        alert_stats = PriceAlert.objects.aggregate(
            total_alerts=Count('id'),
            active_alerts=Count('id', filter=Q(status=PriceAlert.Status.ACTIVE)),
            triggered_alerts=Count('id', filter=Q(status=PriceAlert.Status.TRIGGERED)),
        )
        total_notifications = NotificationLog.objects.aggregate(Count('id'))['id__count']

        top_routes = (
            PriceAlert.objects.values('origin', 'destination')
            .annotate(alert_count=Count('id'))
            .order_by('-alert_count', 'origin', 'destination')[:5]
        )

        return Response({
            **alert_stats,
            'total_notifications': total_notifications,
            'top_routes': [
                {'route': f"{r['origin']}-{r['destination']}", 'alert_count': r['alert_count']}
                for r in top_routes
            ],
        })
