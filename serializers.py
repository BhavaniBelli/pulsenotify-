from django.contrib.auth.models import User
from rest_framework import serializers

from .models import PriceAlert


class RegisterSerializer(serializers.Serializer):
    # NOTE: no `role` field on purpose - role can never come from the request body.
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=6)
    email = serializers.EmailField(required=False, allow_blank=True, default='')

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('A user with that username already exists.')
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class PriceAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceAlert
        fields = ['id', 'origin', 'destination', 'threshold_price', 'status', 'created_at']
        read_only_fields = ['id', 'status', 'created_at']  # user is never accepted from body

    def validate_origin(self, value):
        return value.strip().upper()

    def validate_destination(self, value):
        return value.strip().upper()

    def validate_threshold_price(self, value):
        if value <= 0:
            raise serializers.ValidationError('threshold_price must be greater than 0.')
        return value
