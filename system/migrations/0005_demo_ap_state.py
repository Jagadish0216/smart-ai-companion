from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('system', '0004_networkprovisioningstate')]
    operations = [migrations.AlterField(
        model_name='networkprovisioningstate', name='state',
        field=models.CharField(max_length=20, default='UNPROVISIONED', choices=[
            ('UNPROVISIONED', 'Unprovisioned'), ('SETUP_AP', 'Setup access point'),
            ('CONNECTING', 'Connecting'), ('FAILED', 'Failed'), ('NORMAL_MODE', 'Normal mode'),
            ('DEMO_AP', 'Protected demonstration access point')]))]
