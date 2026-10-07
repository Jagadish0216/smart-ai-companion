from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("knowledge_base", "0002_document_indexing_and_chunks")]
    operations = [
        migrations.AddField(
            model_name="document", name="file_size",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document", name="error_message",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
