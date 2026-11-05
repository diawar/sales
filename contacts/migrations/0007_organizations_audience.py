from django.db import migrations

def add_organizations(apps,schema_editor):
    Audience=apps.get_model('contacts','Audience')
    Audience.objects.get_or_create(name_key='root:организации',defaults={'name':'Организации','parent_id':None})

class Migration(migrations.Migration):
    dependencies=[('contacts','0006_collectionitem_collectionlog_collectionparticipant_and_more')]
    operations=[migrations.RunPython(add_organizations,migrations.RunPython.noop)]
