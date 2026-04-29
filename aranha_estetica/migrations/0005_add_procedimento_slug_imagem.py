from django.db import migrations, models
from django.utils.text import slugify


def gerar_slugs_existentes(apps, schema_editor):
    """Gera slug unico para procedimentos ja cadastrados."""
    Procedimento = apps.get_model('app_shivazen', 'Procedimento')
    usados = set()
    for proc in Procedimento.objects.all():
        base = slugify(proc.nome) or f'procedimento-{proc.pk}'
        slug = base
        counter = 1
        while slug in usados or Procedimento.objects.filter(slug=slug).exclude(pk=proc.pk).exists():
            counter += 1
            slug = f'{base}-{counter}'
        proc.slug = slug
        proc.save(update_fields=['slug'])
        usados.add(slug)


def reverter_slugs(apps, schema_editor):
    Procedimento = apps.get_model('app_shivazen', 'Procedimento')
    Procedimento.objects.update(slug=None)


class Migration(migrations.Migration):

    dependencies = [
        ('app_shivazen', '0004_add_categoria_procedimento'),
    ]

    operations = [
        migrations.AddField(
            model_name='procedimento',
            name='slug',
            field=models.SlugField(blank=True, max_length=140, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='procedimento',
            name='descricao_longa',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='procedimento',
            name='imagem_destaque',
            field=models.URLField(blank=True, max_length=500, null=True),
        ),
        migrations.RunPython(gerar_slugs_existentes, reverter_slugs),
    ]
