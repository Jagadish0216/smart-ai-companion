from rest_framework import serializers

class ChatQuerySerializer(serializers.Serializer):
    query = serializers.CharField(required=True, max_length=1000)
    conversation_id = serializers.IntegerField(required=False, allow_null=True)

    knowledge_mode = serializers.ChoiceField(choices=['online_preferred', 'offline_only', 'online_only'], required=False)
    document_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=100, required=False, allow_null=True)
