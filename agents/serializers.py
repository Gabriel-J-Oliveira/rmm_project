from rest_framework import serializers


class OperatingSystemSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, allow_blank=True, required=False)
    version = serializers.CharField(max_length=128, allow_blank=True, required=False)
    build = serializers.CharField(max_length=128, allow_blank=True, required=False)


class HardwareSerializer(serializers.Serializer):
    cpu = serializers.CharField(max_length=255, allow_blank=True, required=False)
    memory_total_bytes = serializers.IntegerField(min_value=0, required=False)
    manufacturer = serializers.CharField(max_length=255, allow_blank=True, required=False)
    model = serializers.CharField(max_length=255, allow_blank=True, required=False)
    serial_number = serializers.CharField(max_length=255, allow_blank=True, required=False)


class DiskSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    size_bytes = serializers.IntegerField(min_value=0, required=False)
    free_bytes = serializers.IntegerField(min_value=0, required=False)


class InstalledSoftwareSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    version = serializers.CharField(max_length=128, allow_blank=True, required=False)
    publisher = serializers.CharField(max_length=255, allow_blank=True, required=False)


class AgentMetadataSerializer(serializers.Serializer):
    version = serializers.CharField(max_length=50, allow_blank=True, required=False)
    mode = serializers.CharField(max_length=50, allow_blank=True, required=False)
    install_path = serializers.CharField(max_length=255, allow_blank=True, required=False)
    task_name = serializers.CharField(max_length=120, allow_blank=True, required=False)
    runtime = serializers.CharField(max_length=80, allow_blank=True, required=False)
    runtime_version = serializers.CharField(max_length=80, allow_blank=True, required=False)
    update_source = serializers.CharField(max_length=500, allow_blank=True, required=False)
    last_status = serializers.CharField(max_length=120, allow_blank=True, required=False)


class HeartbeatSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField(min_value=1, required=False)
    agent_id = serializers.CharField(max_length=255, allow_blank=True, required=False)
    hostname = serializers.CharField(max_length=255)
    domain = serializers.CharField(max_length=255, allow_blank=True, required=False)
    logged_user = serializers.CharField(max_length=255, allow_blank=True, required=False)
    ips = serializers.ListField(
        child=serializers.IPAddressField(protocol='both'),
        allow_empty=True,
        required=False,
    )
    os = OperatingSystemSerializer(required=False)
    hardware = HardwareSerializer(required=False)
    disks = DiskSerializer(many=True, required=False)
    uptime_seconds = serializers.IntegerField(min_value=0, required=False)
    installed_software = InstalledSoftwareSerializer(many=True, required=False)
    defender_status = serializers.DictField(required=False)
    agent = AgentMetadataSerializer(required=False)
    heartbeat_at = serializers.DateTimeField()


class AgentEnrollmentSerializer(serializers.Serializer):
    enrollment_token = serializers.CharField(max_length=255)
    manual_validation_token = serializers.CharField(max_length=80, allow_blank=True, required=False)
    hostname = serializers.CharField(max_length=150)
    domain = serializers.CharField(max_length=150, allow_blank=True, required=False)
    serial_number = serializers.CharField(max_length=150, allow_blank=True, required=False)
    agent_version = serializers.CharField(max_length=50, allow_blank=True, required=False)
    agent_mode = serializers.CharField(max_length=50, allow_blank=True, required=False)
    install_path = serializers.CharField(max_length=255, allow_blank=True, required=False)
    task_name = serializers.CharField(max_length=120, allow_blank=True, required=False)
