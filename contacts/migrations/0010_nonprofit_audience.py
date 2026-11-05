from django.db import migrations

def rename(apps,schema_editor):
    Audience=apps.get_model('contacts','Audience')
    old=Audience.objects.filter(name_key='root:организации',parent_id=None).first()
    if old:
        if Audience.objects.exclude(pk=old.pk).filter(name_key='root:некоммерческие организации').exists():
            raise RuntimeError('Новая группа уже существует; требуется проверка перед переименованием.')
        old.name='Некоммерческие организации'
        old.name_key='root:некоммерческие организации'
        old.save(update_fields=['name','name_key'])

class Migration(migrations.Migration):
    dependencies=[('contacts','0009_producerpilot')]
    operations=[migrations.RunPython(rename,migrations.RunPython.noop)]
