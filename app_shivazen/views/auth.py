from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Q, Sum, F
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.urls import reverse
import json

from ..models import * 


def usuarioCadastro(request):

    if request.method == 'POST':

        nome = request.POST.get('nome-completo')

        cpf = request.POST.get('cpf')

        email = request.POST.get('email')

        telefone = request.POST.get('telefone')

        senha = request.POST.get('senha')



        # Verifica se Cliente (CPF) ou Usuário (Email) já existem

        if Cliente.objects.filter(cpf=cpf).exists() or Usuario.objects.filter(email=email).exists():

            messages.error(request, 'CPF ou E-mail já cadastrado.')

            return redirect('shivazen:usuarioCadastro')



        try:

            # Cria o Cliente

            novo_cliente = Cliente.objects.create(nome_completo=nome, cpf=cpf, email=email, telefone=telefone)

            # Cria o Prontuário associado

            Prontuario.objects.create(cliente=novo_cliente)



            # Busca ou cria o Perfil de 'Cliente'

            perfil_cliente, _ = Perfil.objects.get_or_create(nome='Cliente', defaults={'descricao': 'Perfil para clientes da clínica.'})

            

            # --- Cria o Usuário (Método Django) ---

            # Usa 'create_user' que cuida automaticamente do hash da senha

            Usuario.objects.create_user(

                username=email, # Usamos email como username também

                email=email,

                password=senha,

                nome=nome, # A tabela usa 'nome' em vez de 'first_name'

                perfil=perfil_cliente,

                is_active=True # is_active substitui o campo 'ativo'

            )



            messages.success(request, 'Cadastro realizado com sucesso! Faça o login.')

            return redirect('shivazen:usuarioLogin')

        

        except Exception as e:

            messages.error(request, f'Ocorreu um erro durante o cadastro: {e}')

            # Se deu erro, tentamos remover o cliente (se foi criado)

            Cliente.objects.filter(cpf=cpf).delete() 

            return redirect('shivazen:usuarioCadastro')



    return render(request, 'usuario/cadastro.html')





def usuarioLogin(request):

    if request.method == 'POST':

        login_identifier = request.POST.get('login') # Pode ser email ou CPF

        senha = request.POST.get('senha')

        email_para_auth = None



        try:

            # 1. Tenta identificar o email

            if '@' in login_identifier:

                email_para_auth = login_identifier

            else:

                # Se não for email, busca o cliente pelo CPF

                try:

                    cliente = Cliente.objects.get(cpf=login_identifier)

                    email_para_auth = cliente.email

                except Cliente.DoesNotExist:

                    pass # Se não achar, o 'authenticate' vai falhar

            

            if not email_para_auth:

                messages.error(request, 'E-mail/CPF ou senha incorretos.')

                return redirect('shivazen:usuarioLogin')



            # 2. Autentica com o sistema do Django (usando 'email' e 'password')

            usuario_autenticado = authenticate(request, email=email_para_auth, password=senha)



            if usuario_autenticado is not None:

                # 3. Faz o login

                auth_login(request, usuario_autenticado) 

                

                # 4. Armazena dados da sessão

                request.session['usuario_id'] = usuario_autenticado.id

                request.session['usuario_nome'] = usuario_autenticado.first_name

                if usuario_autenticado.perfil:

                    request.session['usuario_perfil'] = usuario_autenticado.perfil.nome



                # 5. Redireciona baseado no perfil

                # 'is_staff' é um campo padrão do Django para acesso ao Admin

                if usuario_autenticado.is_staff:

                    messages.success(request, f'Bem-vindo(a), {usuario_autenticado.first_name}!')

                    return redirect('shivazen:adminDashboard')

                else:

                    # Se for cliente, guarda o ID do cliente na sessão

                    try:

                        cliente = Cliente.objects.get(email=usuario_autenticado.email)

                        request.session['cliente_id'] = cliente.id_cliente

                    except Cliente.DoesNotExist:

                        pass # Usuário sem cliente associado

                    messages.success(request, f'Bem-vindo(a), {usuario_autenticado.first_name}!')

                    return redirect('shivazen:painel')

            else:

                # Falha na autenticação

                messages.error(request, 'E-mail/CPF ou senha incorretos.')

                return redirect('shivazen:usuarioLogin')



        except Exception as e:

            messages.error(request, f'Ocorreu um erro inesperado: {e}')

            return redirect('shivazen:usuarioLogin')

            

    return render(request, 'usuario/login.html')







def usuarioLogout(request):

    auth_logout(request) # Usa o logout padrão do Django

    # request.session.flush() # O auth_logout já limpa a sessão de autenticação

    messages.info(request, 'Você saiu da sua conta.')

    return redirect('shivazen:inicio')





def esqueciSenha(request):

    return render(request, 'usuario/esqueciSenha.html')







