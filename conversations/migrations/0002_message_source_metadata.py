from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("conversations", "0001_initial")]
    operations = [migrations.AddField(model_name="message", name="source_metadata", field=models.JSONField(default=dict, blank=True, null=True))]
