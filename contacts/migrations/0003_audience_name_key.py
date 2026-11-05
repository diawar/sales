from django.db import migrations,models

def populate(apps,schema_editor):
    Audience=apps.get_model('contacts','Audience')
    for audience in Audience.objects.all():
        audience.name_key=audience.name.strip().casefold()
        audience.save(update_fields=['name_key'])

class Migration(migrations.Migration):
    dependencies=[('contacts','0002_default_audiences')]
    operations=[migrations.AddField(model_name='audience',name='name_key',field=models.CharField(max_length=240,unique=True,null=True)),migrations.RunPython(populate,migrations.RunPython.noop),migrations.AlterField(model_name='audience',name='name_key',field=models.CharField(max_length=240,unique=True,default=''))]
