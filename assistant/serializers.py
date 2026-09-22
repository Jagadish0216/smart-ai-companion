from rest_framework import serializers

class ChatQuerySerializer(serializers.Serializer):
    query = serializers.CharField(required=True, max_length=1000)
    conversation_id = serializers.IntegerField(required=False, allow_null=True)
