"""Regras operacionais: as chaves do registro tipado (models/configuracao.CHAVES), uma por linha.

A tela lista SO o registro (nao a tabela inteira: marca e estado interno ficam fora) e nao cria chave
livre. Gravar valida pelo tipo/faixa da regra (forms/cadastros.ConfiguracaoForm); "Restaurar padrao"
apaga a linha e a regra volta ao padrao do registro. Nome, contatos e cor: tela Identidade visual
(views/painel/marca). As perguntas do prontuario sao o formulario PRONTUARIO (Anamneses) desde a 0051.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from ...decorators import escrita_painel, staff_required
from ...forms.cadastros import ConfiguracaoForm
from ...forms.painel import avisar_erros
from ...models import Configuracao
from ...models.configuracao import CHAVES, TIPO_EMAIL, chave_do_registro
from ...utils.auditoria import registrar_log


def _recusado(definicao, gravado) -> bool:
    """Valor gravado antes da validacao (ou pelo Django admin) que o registro recusa: vale o padrao."""
    if not gravado.strip():
        return False
    try:
        definicao.validar(gravado)
    except ValidationError:
        return True
    return False


def _regras(recusa=None):
    """Linhas da tela, na ordem do registro. recusa = (chave, digitado, erro) do envio recusado."""
    salvas = Configuracao.das_regras()
    regras = []
    for nome, definicao in CHAVES.items():
        linha = salvas.get(nome)
        gravado = linha.valor if linha is not None else ''
        regra = {
            'chave': nome, 'rotulo': definicao.rotulo, 'descricao': definicao.descricao,
            'email': definicao.tipo == TIPO_EMAIL, 'minimo': definicao.minimo, 'maximo': definicao.maximo,
            'padrao': definicao.default, 'linha': linha, 'valor': gravado,
            'gravado_invalido': _recusado(definicao, gravado),
            'erro': '',
        }
        if recusa is not None and recusa[0] == nome:
            regra['valor'], regra['erro'] = recusa[1], recusa[2]
        regras.append(regra)
    return regras


def _tela(request, recusa=None):
    return render(request, 'painel/configuracoes.html', {'regras': _regras(recusa)})


def _devolver_recusa(request, form):
    """Envio recusado: a tela volta (sem redirect) com o digitado e o erro no campo da regra."""
    avisar_erros(request, form)
    nome = form.cleaned_data.get('chave') or chave_do_registro(form.chave_fixa or form.data.get('chave'))
    erro = (form.errors.get('valor') or [''])[0]
    return _tela(request, recusa=(nome, form.data.get('valor', ''), erro) if nome else None)


def _gravar(request, nome, valor, linha=None):
    definicao = CHAVES[nome]
    texto = str(valor)
    if linha is None:
        linha = Configuracao.das_regras().get(nome)
    antes = linha.valor if linha is not None else None
    if linha is None:
        linha = Configuracao.objects.create(chave=nome, valor=texto, descricao=definicao.descricao)
        registrar_log(request.user, f'Criou configuracao: {nome}', 'configuracao', linha.pk,
                      detalhes={'valor': texto}, request=request)
    else:
        linha.valor, linha.descricao = texto, definicao.descricao
        linha.save(update_fields=['valor', 'descricao', 'atualizado_em'])
        registrar_log(request.user, f'Editou configuracao: {linha.chave}', 'configuracao', linha.pk,
                      detalhes={'valor_antigo': antes, 'valor_novo': texto}, request=request)
    messages.success(request, f'Regra "{definicao.rotulo}" atualizada.')
    return redirect('aranha:admin_configuracoes')


def _linha_do_registro(pk):
    """Linha gravada de uma regra do registro; outra chave (marca, legado) nao e editavel aqui: 404."""
    linha = get_object_or_404(Configuracao, pk=pk)
    if chave_do_registro(linha.chave) is None:
        raise Http404('Configuração fora das regras operacionais')
    return linha


@staff_required
def admin_configuracoes(request):
    """Lista as regras operacionais (o registro), com o valor gravado ou o padrao."""
    return _tela(request)


@escrita_painel
def admin_criar_configuracao(request):
    """Primeira gravacao de uma regra do registro (chave livre e recusada)."""
    form = ConfiguracaoForm(request.POST)
    if not form.is_valid():
        return _devolver_recusa(request, form)
    return _gravar(request, form.cleaned_data['chave'], form.cleaned_data['valor'])


@escrita_painel
def admin_editar_configuracao(request, pk):
    """Novo valor de uma regra ja gravada."""
    linha = _linha_do_registro(pk)
    form = ConfiguracaoForm(request.POST, chave=linha.chave)
    if not form.is_valid():
        return _devolver_recusa(request, form)
    return _gravar(request, form.cleaned_data['chave'], form.cleaned_data['valor'], linha=linha)


@escrita_painel
def admin_excluir_configuracao(request, pk):
    """Restaurar padrao: apaga a linha da regra (vale o padrao do registro)."""
    linha = _linha_do_registro(pk)
    definicao = CHAVES[chave_do_registro(linha.chave)]
    chave = linha.chave
    linha.delete()
    registrar_log(request.user, f'Excluiu configuracao: {chave}', 'configuracao', pk, request=request)
    messages.success(request, f'Regra "{definicao.rotulo}" voltou ao padrão.')
    return redirect('aranha:admin_configuracoes')
