from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("knowledge_base", "0004_document_online_sync")]
    operations = [migrations.AddField(model_name="knowledgechunk", name="page_number", field=models.PositiveIntegerField(null=True, blank=True))]
