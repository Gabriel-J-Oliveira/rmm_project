from rest_framework import serializers

from .models import (
    ADGroup,
    ADGroupMembership,
    ADOrganizationalUnit,
    ADUser,
    AclEntry,
    FileServer,
    Folder,
    Share,
)


class ADOrganizationalUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = ADOrganizationalUnit
        fields = '__all__'


class ADUserSerializer(serializers.ModelSerializer):
    ou_name = serializers.CharField(source='ou.name', read_only=True)

    class Meta:
        model = ADUser
        fields = '__all__'


class ADGroupSerializer(serializers.ModelSerializer):
    ou_name = serializers.CharField(source='ou.name', read_only=True)

    class Meta:
        model = ADGroup
        fields = '__all__'


class ADGroupMembershipSerializer(serializers.ModelSerializer):
    parent_group_name = serializers.CharField(source='parent_group.name', read_only=True)
    member_user_name = serializers.CharField(source='member_user.display_name', read_only=True)
    member_group_name = serializers.CharField(source='member_group.name', read_only=True)

    class Meta:
        model = ADGroupMembership
        fields = '__all__'


class FileServerSerializer(serializers.ModelSerializer):
    rmm_hostname = serializers.CharField(source='rmm_agent.hostname', read_only=True)

    class Meta:
        model = FileServer
        fields = '__all__'


class ShareSerializer(serializers.ModelSerializer):
    file_server_name = serializers.CharField(source='file_server.name', read_only=True)

    class Meta:
        model = Share
        fields = '__all__'


class FolderSerializer(serializers.ModelSerializer):
    share_unc_path = serializers.CharField(source='share.unc_path', read_only=True)
    file_server_name = serializers.CharField(source='share.file_server.name', read_only=True)

    class Meta:
        model = Folder
        fields = '__all__'


class AclEntrySerializer(serializers.ModelSerializer):
    folder_path = serializers.CharField(source='folder.path', read_only=True)
    ad_user_name = serializers.CharField(source='ad_user.display_name', read_only=True)
    ad_group_name = serializers.CharField(source='ad_group.name', read_only=True)

    class Meta:
        model = AclEntry
        fields = '__all__'
