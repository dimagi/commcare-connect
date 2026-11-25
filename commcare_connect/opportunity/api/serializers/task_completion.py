from rest_framework import serializers


class TaskCompletionSerializer(serializers.Serializer):
    connectTaskId = serializers.UUIDField()
    completed_at = serializers.DateTimeField(required=False)
