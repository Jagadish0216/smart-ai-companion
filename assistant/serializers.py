from rest_framework import serializers

class ChatQuerySerializer(serializers.Serializer):
    query = serializers.CharField(required=True, max_length=1000)
    knowledge_mode = serializers.ChoiceField(choices=['online_preferred', 'offline_only', 'online_only'], required=False)
    document_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=50, required=False)

    def validate_document_ids(self, values):
        from knowledge_base.models import Document
        if Document.objects.filter(pk__in=set(values), status='INDEXED').count() != len(set(values)):
            raise serializers.ValidationError('Selected documents are missing or not indexed. Refresh the document list.')
        return list(dict.fromkeys(values))

    conversation_id = serializers.IntegerField(required=False, allow_null=True)
