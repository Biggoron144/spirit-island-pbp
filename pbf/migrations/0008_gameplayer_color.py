# Generated by Django 4.0.1 on 2022-01-26 03:19

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pbf', '0007_merge_20220126_0305'),
    ]

    operations = [
        migrations.AddField(
            model_name='gameplayer',
            name='color',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
