from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_base", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="indexed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="source_identifier",
            field=models.CharField(blank=True, db_index=True, max_length=1024),
        ),
        migrations.CreateModel(
            name="KnowledgeChunk",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("chunk_index", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="knowledge_base.document",
                    ),
                ),
            ],
            options={
                "ordering": ("document_id", "chunk_index"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("document", "chunk_index"),
                        name="unique_document_chunk_index",
                    ),
                ],
            },
        ),
    ]
