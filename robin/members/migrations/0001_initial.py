# -*- coding: utf-8 -*-
# Generated by Django 1.9.9 on 2016-09-22 03:33
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Member',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('create_date', models.DateTimeField(auto_now_add=True, verbose_name='create date')),
                ('modified_date', models.DateTimeField(auto_now=True, verbose_name='modified date')),
                ('name', models.CharField(db_index=True, max_length=32, unique=True, verbose_name='member name')),
                ('kerbroes_id', models.CharField(max_length=32, unique=True, verbose_name='Kerbroes ID')),
                ('rh_email', models.EmailField(max_length=254, unique=True, verbose_name='RedHat email')),
                ('github_account', models.CharField(max_length=32, unique=True, verbose_name='GitHub account')),
            ],
            options={
                'verbose_name': 'member',
                'verbose_name_plural': 'members',
            },
        ),
        migrations.CreateModel(
            name='Team',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('create_date', models.DateTimeField(auto_now_add=True, verbose_name='create date')),
                ('modified_date', models.DateTimeField(auto_now=True, verbose_name='modified date')),
                ('team_name', models.CharField(max_length=32, unique=True, verbose_name='team name')),
                ('team_code', models.CharField(max_length=32, unique=True, verbose_name='team code')),
            ],
            options={
                'verbose_name': 'team',
                'verbose_name_plural': 'teams',
            },
        ),
        migrations.AddField(
            model_name='member',
            name='team',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='members.Team', verbose_name='team'),
        ),
    ]