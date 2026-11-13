# Claude Code + Codex no VS Code

Configuração deste repositório:

| Papel | Modelo | Configuração |
| --- | --- | --- |
| Orquestração | Claude Opus 5.5 (`claude-opus-5-5`), esforço medium | `.claude/settings.json` |
| Execução delegada | GPT-6 Luna (`gpt-6-luna`), esforço medium | `tools/codex_luna_mcp.py` |
| Conexão | MCP stdio `codex_luna` | `.mcp.json` |

## Usar no VS Code

1. Abra esta pasta como workspace e execute **Developer: Reload Window**.
2. Abra Claude Code e inicie uma conversa nova. Confira **Opus 5.5** no
   seletor de modelo.
3. Confira `codex_luna` nas conexões MCP do Claude Code. No terminal,
   `claude mcp list` também verifica a conexão; na CLI do Claude, `/mcp`
   mostra as ferramentas.
4. Peça sua tarefa normalmente ou use, por exemplo:

   ```text
   /orquestrar Investigue com um Luna como o app retoma downloads e me explique os arquivos envolvidos, sem alterar nada.
   ```

O Opus usa a ferramenta `luna_task` para tarefas delimitadas. São permitidas
até três investigações independentes em paralelo; chamadas que escrevem
são serializadas. O padrão é `read-only`, e implementação usa
`workspace-write`. O Opus precisa aguardar o executor antes de alterar os
mesmos arquivos. Cada chamada recebe um contexto novo e tem limite de nove
minutos. Uma falha é devolvida ao Opus, sem substituição automática do modelo.

A extensão Codex pode continuar sendo usada para conversas próprias. A
delegação acontece pela CLI e não aparece como uma conversa compartilhada
entre os dois painéis. Evite iniciar outra edição sobre arquivos que o
Claude ou um Luna esteja alterando.

## Instalação e autenticação

São necessários Python 3, a CLI `codex` no PATH, e as extensões oficiais
`anthropic.claude-code` e `openai.chatgpt`. A ponte usa somente a biblioteca
padrão do Python; as dependências do aplicativo não mudam.

A ponte compara as versões da CLI no PATH e das CLIs incluídas nas
extensões Codex em `~/.vscode/extensions`, e usa a mais recente. Nesta
instalação, a CLI do terminal 0.144.1 recusou `gpt-6-luna` com login ChatGPT;
a CLI da extensão 0.162.0-alpha.2 executou o modelo com o mesmo login.

Verifique os logins sem imprimir credenciais:

```bash
codex login status
claude auth status
claude mcp list
```

Se necessário, autentique-se com `codex login` e pelo botão de login do
Claude Code. Chamadas aos modelos dependem do acesso e dos limites de cada
conta. O uso dos Lunas passa pela autenticação existente do Codex.

`.claude/settings.local.json` aprova especificamente esta ponte e permite
a ferramenta. É uma configuração pessoal ignorada pelo Git. Em outro
computador, aprove `codex_luna` no Claude Code antes de usar.

## Arquivos e manutenção

- `CLAUDE.md`: instruções de orquestração do Claude.
- `AGENTS.md`: responsabilidades do Codex, inclusive como executor.
- `.claude/commands/orquestrar.md`: comando de início de tarefa.
- `.vscode/`: recomendações de extensões e posição do painel Claude.
- `tests/test_codex_luna_mcp.py`: verificações da ponte sem chamadas pagas.

Execute as verificações locais com:

```bash
python3 -m unittest discover -s tests -p 'test_codex_luna_mcp.py' -v
```

Na configuração de 8 de outubro de 2026, os sete testes passaram. Também
foram verificados `claude mcp list` com a ponte conectada, uma chamada real
de Opus 5.5 ao Luna sem recusas de permissão e uma execução Luna que leu
`requirements.txt` e escreveu um arquivo temporário. O conteúdo escrito
foi conferido e o diretório temporário foi removido. Versões locais usadas:
Codex CLI 0.144.1 e Claude Code CLI 2.1.290.

Após selecionar GPT-6 Luna, foi validada uma chamada real ao novo modelo
usando a CLI 0.162.0-alpha.2 da extensão, incluindo leitura de
`requirements.txt`. Os testes da ponte também passaram. Na última tentativa
de verificar novamente a cadeia completa, o Claude respondeu
`Your organization has disabled Claude subscription access for Claude Code`.
A execução direta do GPT-6 Luna pela ponte continuou funcionando; a
orquestração pelo Opus depende de liberar esse acesso no Claude Code.

A ponte chama `codex exec` e não depende do antigo `codex mcp-server`.
Se o MCP não conectar, execute `python3 tools/codex_luna_mcp.py` a partir
da raiz para identificar erros de inicialização (ele espera mensagens MCP
no stdin), confirme que `codex --version` funciona e recarregue o VS Code.
Se a execução falhar, confira o acesso a `gpt-6-luna` antes de tentar novamente.

Referências oficiais:

- [Modelos e seleção no Claude Code](https://code.claude.com/docs/en/model-config).
- [Conexões MCP do Claude Code](https://code.claude.com/docs/en/mcp).
- [Comandos do Codex, incluindo exec](https://learn.chatgpt.com/docs/developer-commands?surface=cli).
- [Remoção do servidor MCP antigo](https://learn.chatgpt.com/docs/mcp-server).
