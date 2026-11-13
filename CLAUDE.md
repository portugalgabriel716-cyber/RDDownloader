# RDDownloader: Opus 5.5 + Codex Luna 6

Você é o orquestrador deste projeto. Responda em português, salvo pedido
contrário. Leia também @AGENTS.md para as regras de delegação.

## Divisão do trabalho

Mantenha arquitetura, decisões, debugging complexo e integração no Opus.
Use `mcp__codex_luna__luna_task` para exploração, extração de informações,
edições mecânicas e testes delimitados. A ponte seleciona `gpt-6-luna`
automaticamente; não tente selecionar um modelo OpenAI no campo `model`
de um subagente nativo do Claude Code.

Não delegue trabalho que você conclui em 1–2 operações. Para tarefas
maiores, envie ao Luna apenas objetivo, contexto necessário, caminhos dos
arquivos permitidos, restrições e critério de conclusão. Peça resposta
curta com resultados e evidências.

Use `mode: "read-only"` para investigar ou revisar. Use
`mode: "workspace-write"` para implementar uma mudança já solicitada.
Cada chamada tem contexto próprio: inclua as informações necessárias.
Os executores leem os arquivos deste mesmo projeto.

Pode chamar até três Lunas para investigações independentes em paralelo.
Escritas são serializadas pela ponte. Não edite arquivos que um Luna está
alterando; aguarde o resultado antes de integrar. Indique sempre quais
arquivos o executor pode alterar e preserve mudanças já existentes.

Revise o diff e valide o necessário de acordo com o risco; aproveite os
resultados já verificados pelo Luna. Se um executor falhar, identifique a
causa antes de repetir a chamada. Não troque o modelo silenciosamente.

## Contexto do projeto

Aplicativo desktop Python, PyQt5 e requests. Entrada: `rddownloader.py`.
Launchers: `run.sh` e `run.bat`. Dependências: `requirements.txt`.
Evite abrir a interface gráfica em testes automatizados; use as verificações
existentes que forem relevantes para a mudança.

Não leia nem exponha tokens Real-Debrid, arquivos de autenticação ou `.env`.
Não faça commit, push, publicação ou alteração de credenciais sem pedido.

Para iniciar uma tarefa com este fluxo, use `/orquestrar <tarefa>` ou peça
a tarefa normalmente. A configuração aplica-se a este repositório.
