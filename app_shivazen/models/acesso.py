# app_shivazen/models/acesso.py — Controle de acesso e usuarios
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.models import update_last_login
from django.db import models

# Desconecta sinal que tenta atualizar last_login (nao presente no schema customizado)
user_logged_in.disconnect(update_last_login, dispatch_uid='update_last_login')


class Funcionalidade(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    descricao = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'funcionalidade'

    def __str__(self):
        return self.nome


class Perfil(models.Model):
    nome = models.CharField(max_length=50, unique=True)
    descricao = models.TextField(blank=True, null=True)
    funcionalidades = models.ManyToManyField(Funcionalidade, through='PerfilFuncionalidade')

    class Meta:
        managed = True
        db_table = 'perfil'

    def __str__(self):
        return self.nome


class PerfilFuncionalidade(models.Model):
    perfil = models.ForeignKey(Perfil, on_delete=models.CASCADE)
    funcionalidade = models.ForeignKey(Funcionalidade, on_delete=models.CASCADE)

    class Meta:
        managed = True
        db_table = 'perfil_funcionalidade'
        unique_together = (('perfil', 'funcionalidade'),)


class UsuarioManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('O email deve ser definido')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('ativo', True)
        user = self.create_user(email, password, **extra_fields)
        from django.apps import apps
        PerfilModel = apps.get_model('app_shivazen', 'Perfil')
        perfil_admin, _ = PerfilModel.objects.get_or_create(
            nome='Administrador',
            defaults={'descricao': 'Acesso total ao sistema'}
        )
        user.perfil = perfil_admin
        user.save(update_fields=['perfil_id'])
        return user


class Usuario(AbstractBaseUser):
    perfil = models.ForeignKey(Perfil, on_delete=models.RESTRICT, null=True, blank=True)
    profissional = models.OneToOneField(
        'Profissional', on_delete=models.SET_NULL, null=True, blank=True
    )
    nome = models.CharField(max_length=100)
    email = models.EmailField(max_length=100, unique=True)
    password = models.CharField(max_length=255, db_column='senha_hash')
    ativo = models.BooleanField(default=True)

    last_login = None

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nome']
    objects = UsuarioManager()

    class Meta:
        managed = True
        db_table = 'usuario'

    @property
    def is_active(self):
        return self.ativo

    @property
    def is_staff(self):
        return bool(self.perfil and self.perfil.nome == 'Administrador')

    @property
    def first_name(self):
        return self.nome

    def has_perm(self, perm, obj=None):
        return self.is_staff

    def has_module_perms(self, app_label):
        return self.is_staff
