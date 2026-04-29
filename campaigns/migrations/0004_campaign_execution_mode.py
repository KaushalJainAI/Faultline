from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0003_campaign_auth_flow"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaign",
            name="execution_mode",
            field=models.CharField(
                choices=[
                    ("pipeline", "Pipeline First"),
                    ("agent", "Agent First"),
                    ("hybrid", "Hybrid"),
                ],
                default="hybrid",
                max_length=20,
            ),
        ),
    ]
