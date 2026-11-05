from django.db import migrations,models
import django.db.models.deletion

def populate(apps,schema_editor):
    Audience=apps.get_model('contacts','Audience')
    for row in Audience.objects.all():
        row.name_key='legacy:'+str(row.pk)
        row.save(update_fields=['name_key'])
    roots={}
    for name in ['Бизнес','Физические лица','Государственные структуры']:
        row=Audience.objects.filter(name=name).first()
        if row is None:
            row=Audience.objects.create(name=name,name_key='root:'+name.casefold())
        roots[name]=row
    for row in Audience.objects.all():
        row.parent_id=None if row.name in roots else roots['Бизнес'].pk
        row.name_key=str(row.parent_id or 'root')+':'+row.name.strip().casefold()
        row.save(update_fields=['parent_id','name_key'])

class Migration(migrations.Migration):
    dependencies=[('contacts','0003_audience_name_key')]
    operations=[migrations.AddField(model_name='audience',name='parent',field=models.ForeignKey(null=True,blank=True,on_delete=django.db.models.deletion.PROTECT,related_name='children',to='contacts.audience')),migrations.AlterField(model_name='audience',name='name',field=models.CharField(max_length=120)),migrations.RunPython(populate,migrations.RunPython.noop)]
