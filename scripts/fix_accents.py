"""Fix Portuguese accents in shivazen templates — apenas TEXTO VISÍVEL.

Skip blocks:
  - {% ... %}  Django tags
  - {{ ... }}  Django expressions
  - <script>...</script>
  - <style>...</style>
  - HTML attributes (texto entre " ou ' dentro de <...>)
  - linhas com chave JSON ("key": ou "key" =)

So substitui texto entre tags HTML (ou linhas plain).
"""
import os
import re

# Mapping conservador. Lowercase + Capitalized variants.
# So palavras com forte chance de aparecer em copy user-facing.
mapping = {
    "voce": "você", "Voce": "Você",
    "vocês": "vocês",
    "codigo": "código", "Codigo": "Código",
    "codigos": "códigos", "Codigos": "Códigos",
    "digitos": "dígitos", "Digitos": "Dígitos",
    "numero": "número", "Numero": "Número",
    "numeros": "números",
    "historico": "histórico", "Historico": "Histórico",
    "proximo": "próximo", "Proximo": "Próximo",
    "proxima": "próxima", "Proxima": "Próxima",
    "proximos": "próximos",
    "proximas": "próximas",
    "previa": "prévia",
    "periodo": "período", "Periodo": "Período",
    "periodos": "períodos",
    "minimo": "mínimo", "Minimo": "Mínimo",
    "minima": "mínima",
    "maximo": "máximo", "Maximo": "Máximo",
    "maxima": "máxima",
    "rapido": "rápido", "Rapido": "Rápido",
    "rapida": "rápida",
    "pagina": "página",
    "paginas": "páginas",
    "tres": "três", "Tres": "Três",
    "voltarao": "voltarão",
    "serao": "serão",
    "irao": "irão",
    "estao": "estão",
    "sao": "são",
    "nao": "não", "Nao": "Não",
    "tambem": "também", "Tambem": "Também",
    "porem": "porém",
    "apos": "após", "Apos": "Após",
    "devera": "deverá",
    "deverao": "deverão",
    "permitira": "permitirá",
    "tera": "terá",
    "valido": "válido", "Valido": "Válido",
    "valida": "válida",
    "invalido": "inválido", "Invalido": "Inválido",
    "invalida": "inválida",
    "ultimo": "último", "Ultimo": "Último",
    "ultima": "última", "Ultima": "Última",
    "ultimos": "últimos",
    "ultimas": "últimas",
    "preco": "preço", "Preco": "Preço",
    "precos": "preços",
    "servico": "serviço", "Servico": "Serviço",
    "servicos": "serviços", "Servicos": "Serviços",
    "esteticos": "estéticos",
    "estetico": "estético",
    "estetica": "estética", "Estetica": "Estética",
    "horario": "horário", "Horario": "Horário",
    "horarios": "horários", "Horarios": "Horários",
    "sessao": "sessão",
    "sessoes": "sessões", "Sessoes": "Sessões",
    "presenca": "presença", "Presenca": "Presença",
    "agencia": "agência",
    "analise": "análise",
    "duvida": "dúvida",
    "duvidas": "dúvidas",
    "experiencia": "experiência",
    "experiencias": "experiências",
    "satisfacao": "satisfação",
    "avaliacao": "avaliação", "Avaliacao": "Avaliação",
    "avaliacoes": "avaliações",
    "integracao": "integração",
    "integracoes": "integrações", "Integracoes": "Integrações",
    "verificacao": "verificação", "Verificacao": "Verificação",
    "aprovacao": "aprovação", "Aprovacao": "Aprovação",
    "rejeicao": "rejeição",
    "informacao": "informação",
    "informacoes": "informações",
    "confirmacao": "confirmação", "Confirmacao": "Confirmação",
    "atencao": "atenção", "Atencao": "Atenção",
    "mudanca": "mudança",
    "mudancas": "mudanças",
    "seguranca": "segurança", "Seguranca": "Segurança",
    "endereco": "endereço", "Endereco": "Endereço",
    "execucao": "execução",
    "obrigatorio": "obrigatório",
    "obrigatoria": "obrigatória",
    "padrao": "padrão",
    "padroes": "padrões",
    "questionario": "questionário",
    "saude": "saúde",
    "ferias": "férias",
    "descricao": "descrição",
    "descricoes": "descrições",
    "observacao": "observação",
    "observacoes": "observações",
    "configuracao": "configuração",
    "configuracoes": "configurações",
    "geracao": "geração",
}

# Skip files que tem alta chance de quebrar
SKIP_FILES = set()

# Skip palavras que aparecem em var names Django/Python ou model fields conhecidos
PROTECTED_IDENTIFIERS = {
    # model fields
    "preco", "servico", "servicos", "horario", "horarios",
    "sessao", "sessoes", "presenca", "endereco", "descricao", "descricoes",
    "observacao", "observacoes", "configuracao", "configuracoes",
    "atencao", "informacao", "informacoes", "confirmacao", "execucao",
    "obrigatorio", "obrigatoria", "avaliacao", "avaliacoes",
    "verificacao", "aprovacao", "rejeicao", "satisfacao", "integracao",
    "integracoes", "seguranca", "agencia", "duvida", "duvidas",
    "experiencia", "experiencias", "questionario", "geracao",
    "mudanca", "mudancas", "saude", "ferias", "rapido", "rapida",
    "pagina", "paginas", "estetico", "estetica", "esteticos",
    "minimo", "maximo", "maxima", "minima", "tres", "previa",
    "proximo", "proxima", "proximos", "proximas", "periodo", "periodos",
    "ultimo", "ultima", "ultimos", "ultimas", "valido", "valida",
    "invalido", "invalida", "historico", "tera", "devera", "deverao",
    "permitira", "voltarao", "serao", "irao", "estao", "sao",
    "tambem", "porem", "apos", "padrao", "padroes",
    "voce", "codigo", "codigos", "digitos", "numero", "numeros",
}

# Regex p/ identificar contexto onde NUNCA aplicar:
# - {% ... %}, {{ ... }}, attrs HTML, <script>, <style>, JSON keys
RE_DJANGO_TAG = re.compile(r"{%[^%]*%}")
RE_DJANGO_VAR = re.compile(r"{{[^}]*}}")
RE_SCRIPT_BLOCK = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
RE_STYLE_BLOCK = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
RE_HTML_ATTR = re.compile(r"""<[^>]+>""", re.DOTALL)


def replace_in_text_segments(content: str) -> str:
    """Replace mapping only in HTML text nodes outside special blocks."""
    # Mascarar regions que NAO podem ser substituidas
    placeholders = []

    def stash(match):
        placeholders.append(match.group(0))
        return f"\x00PH{len(placeholders)-1}\x00"

    # Ordem importante: maior antes (script/style) p/ nao matchar tags dentro
    masked = RE_SCRIPT_BLOCK.sub(stash, content)
    masked = RE_STYLE_BLOCK.sub(stash, masked)
    # Tags HTML inteiras (incluindo atributos)
    masked = RE_HTML_ATTR.sub(stash, masked)
    # Django tags/vars (alguns sobreviveram dentro de texto plano)
    masked = RE_DJANGO_TAG.sub(stash, masked)
    masked = RE_DJANGO_VAR.sub(stash, masked)

    # Aplicar substituicoes word-boundary
    for ascii_word, accented in mapping.items():
        pattern = r"\b" + re.escape(ascii_word) + r"\b"
        masked = re.sub(pattern, accented, masked)

    # Restaurar placeholders
    def restore(match):
        idx = int(match.group(1))
        return placeholders[idx]
    masked = re.sub(r"\x00PH(\d+)\x00", restore, masked)

    return masked


def main():
    target_dir = os.path.join("app_shivazen", "templates")
    files = []
    for root, _, fs in os.walk(target_dir):
        for f in fs:
            if f.endswith(".html"):
                fp = os.path.join(root, f)
                if fp not in SKIP_FILES:
                    files.append(fp)

    print(f"arquivos: {len(files)}")
    files_changed = 0
    for fp in files:
        with open(fp, "r", encoding="utf-8") as fh:
            content = fh.read()
        new_content = replace_in_text_segments(content)
        if new_content != content:
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write(new_content)
            files_changed += 1
            print(f"  fixed: {fp}")
    print(f"TOTAL: {files_changed} arquivos modificados")


if __name__ == "__main__":
    main()
