from django.db import migrations

def seed(apps, schema_editor):
    Audience=apps.get_model('contacts','Audience')
    for name in ['Бизнес','Физические лица']:
        Audience.objects.get_or_create(name=name)

class Migration(migrations.Migration):
    dependencies=[('contacts','0001_initial')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
