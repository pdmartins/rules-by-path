# Implementação das recomendações da auditoria de literatura (2026-08-19)

Origem: auditoria multi-agente do design do plugin contra a literatura 2025–2026
(relatório: artifact "Auditoria rules-by-path"). Escopo desta rodada: P0 + P1.
Fora de escopo (P2, próxima rodada): lint de co-injeção de globs, telemetria de
injeção, nota de staleness no validate.

## Contexto e restrições globais (valem para TODAS as tarefas)

- Repo: `/repos/_pm/rules-by-path`, branch `develop`. O working tree tem trabalho
  NÃO COMMITADO em andamento (config layer: `config.py`, `config.json`,
  `scripts/rules_by_path_admin/`, `tests/test_config.py`). Construir EM CIMA dele.
  NUNCA reverter, stash, commitar ou tocar em estado git.
- Pacote do hook (`plugins/rules-by-path/hooks/rules_by_path/`): stdlib apenas,
  Python 3.8+, todo arquivo < 400 linhas (quebrar em módulo novo se estourar),
  fail-open absoluto (exceção ⇒ 1 linha no stderr via `warn()`, exit 0, tool call
  intocada).
- Código, identificadores e comentários em inglês. Sem dead code, sem TODO.
- Constantes novas em `constants.py`, agrupadas com as existentes.
- Config: camadas de projeto são UNTRUSTED — todo campo novo de config lido de
  camada não confiável precisa de sanitização/clamp em `config.py`, seguindo o
  padrão existente.
- Testes: a suíte existente roda o hook como subprocesso com HOME sobrescrito
  (ver `tests/util`). Toda tarefa adiciona testes no arquivo de teste correto e
  roda a suíte COMPLETA (descobrir o runner do repo; default
  `python3 -m pytest tests/ -q`) até verde. Não deletar testes existentes;
  atualizar os que codificam o comportamento alterado.
- Detalhes citados abaixo (assinaturas, formatos de entry, números de linha)
  vêm da leitura de auditoria — VERIFICAR contra o código real antes de usar.
- Não editar `CHANGELOG.md` exceto onde a tarefa mandar explicitamente.
- Não tocar em `.claude/tasks/todo.md` (o orquestrador atualiza).

Estado da sessão (como está hoje, verificar em `state.py`):
- arquivo por sessão com flock; `{"calls": int, "seen": {key: [call, tokens]}}`;
  `context_size()` lê os últimos 64KB do transcript e soma
  input + cache_creation + cache_read + output do ÚLTIMO registro "usage"
  (contagem cobrada; monotônica dentro da sessão, salvo compact/clear);
  `is_due()` decide repetição; sweep de state com 14 dias.
- dedup key: `realpath(scope_dir)::name::sha256(truncated_body)[:16]`.
- Reset de sessão: SessionStart(compact|clear) **async** chama `--reset-session`
  que deleta o arquivo de state.

---

## Tarefa A (P0) — Fallback anti-corrida de compaction

Problema: o reset async de SessionStart(compact|clear) pode perder a corrida para
o PreToolUse seguinte; `seen` stale suprime re-injeção exatamente quando o texto
da regra foi resumido para fora do contexto (evidência: violações 0%→30–59%
pós-compaction, arXiv:2606.22528; arXiv:2608.06503).

Implementação (em `state.py`, chamado a partir do fluxo de `main()` onde state e
`context_size()` já estão disponíveis):
1. Nova constante em `constants.py`: `TOKEN_REGRESSION_SLACK = 4096` (comentário
   de uma linha: regressão além disso = compaction/clear perdeu a corrida).
2. Função `detect_context_regression(state, current_tokens)` (nome livre,
   coerente com o estilo): se `current_tokens` não é None e existe ao menos um
   seen entry com tokens registrado, calcular `max_recorded` sobre os entries
   (coagir formatos com o helper existente de coerção); se
   `current_tokens + TOKEN_REGRESSION_SLACK < max_recorded`, limpar `seen{}`
   IN PLACE (preservar `calls`), emitir `warn()` explicando, retornar True.
3. Chamar no ponto certo do fluxo: depois de carregar state e computar
   `context_size()`, antes das decisões de injeção/dedup.
4. `current_tokens is None` (transcript ilegível) ⇒ nunca limpar.
5. O reset async existente continua sendo o caminho primário — não mexer nele.

Testes (test_hook.py ou test_state se existir): (a) state com tokens altos
gravados + transcript pequeno ⇒ seen limpo e regra re-injeta na mesma chamada;
(b) tokens atuais ≥ máximo gravado ⇒ nada muda; (c) transcript ilegível ⇒ nada
muda; (d) `calls` sobrevive à limpeza.

Aceite: suíte completa verde; regra volta a injetar imediatamente após
compaction simulada mesmo sem o reset async ter rodado.

## Tarefa B (P0) — Docs: claims stale, bug do status, comparação com nativo

1. `README.md`: corrigir DOIS claims que contradizem o código shipped
   (localizar por grep, linhas ~203 e ~334 na leitura de auditoria):
   a. "Design guarantees" ainda cita o bloqueio de nested-CLAUDE.md ("The only
      deliberate block is the nested-CLAUDE.md") — esse guard foi removido.
   b. Troubleshooting diz que o walk de escopos "stops at the repo root" — o
      walk agora vai até a raiz do filesystem (submódulos herdam regras do pai).
2. `plugins/rules-by-path/commands/status.md`, passo 5: o glob de arquivos de
   state não bate com nenhuma localização real de state — corrigir para as
   localizações reais (ler `state.py` para a resolução de diretório).
3. `README.md`: nova seção de comparação com as regras nativas do Claude Code
   (`.claude/rules/*.md` com `paths:`, nested CLAUDE.md com lazy-load). Conteúdo:
   - O que o nativo já faz: JIT por path em Read, reload pós-compact, escopo
     de usuário `~/.claude/rules/`.
   - As 3 lacunas que o plugin cobre: (1) trigger em Write/Edit/arquivo novo
     (recusado upstream: anthropics/claude-code#38487, closed not-planned);
     (2) reforço/re-injeção contra decaimento medido em sessão longa;
     (3) tooling de validação/auditoria (lint, admin CLI).
     Sobre escopo global: alegar apenas "regras path-scoped globais CONFIÁVEIS"
     (houve bugs nativos reportados, ex. #17204 — diferenciador que pode derreter).
   - Claim honesto de valor: aderência a convenções + economia de tokens;
     explicitamente NÃO correção de tarefas (dois ablations 2026 limitam o
     efeito perto de zero).
4. Varrer `skills/*/SKILL.md` e demais docs por repetições dos claims stale de
   (1) e corrigir.
5. `CHANGELOG.md`: seção Unreleased (criar se preciso) com bullets das tarefas
   A e B (só dessas duas).

Aceite: grep não encontra mais os claims antigos; passo 5 do status aponta para
paths reais; seção comparativa presente; tom honesto (sem promessa de correctness).

## Tarefa C (P1) — Reforço por tipo de constraint + orçamento global

Evidência: só constraints de PROIBIÇÃO decaem (73%→33%, arXiv:2604.20911);
requisitos/convenções seguram ~100%. Cada re-injeção soma na contagem de
instruções em contexto — a variável de colapso (arXiv:2608.02639). Logo: reforço
default por TIPO, e teto global de re-injeções.

1. Defaults por tipo no `config.json` do plugin (ler os `rule_types` existentes;
   o default de `remember_again_after` do tipo é gravado no arquivo da regra
   pelo admin CLI no `add` — o hook não conhece tipos):
   - tipos de natureza proibição/política (ex. BUSN e afins) ⇒ manter/ganhar
     default de reforço ativo;
   - tipos de convenção/estilo/arquitetura ⇒ default `0` (never).
   Documentar a racional em uma frase no README (tabela de tipos, se houver).
2. Orçamento global de re-injeções:
   - `constants.py`: `MAX_REINJECTIONS_PER_RULE = 3` (primeira injeção NÃO conta).
   - Config key `reinject_budget` (merge/clamp em `config.py`: int 0–20;
     camada untrusted clampada como as demais).
   - Estender o seen entry `[call, tokens]` → `[call, tokens, reinjections]`
     usando o helper de coerção existente para migrar formato antigo (entry
     velho ⇒ reinjections=0).
   - `is_due()` (ou o chamador) retorna False quando o contador atingiu o
     orçamento; incrementar o contador a cada re-injeção efetivada.
3. Lint no `validate.py` do admin (NUNCA no hook): regra cujo corpo contém
   frase de proibição (regex case-insensitive: `never|do not|don't|must not|
   forbidden|nunca|não (deve|pode)|proibido`) mas com `remember_again_after`
   0/never ⇒ NOTE sugerindo ativar reforço; regra sem frase de proibição e com
   intervalo agressivo ⇒ NOTE de possível sobre-tratamento. Seguir o padrão
   notes-vs-errors existente.

Testes: coerção de entry antigo; orçamento respeitado (re-injeta até N, depois
para; primeira injeção fora da conta); clamp do config key de camada untrusted;
lint dispara e não dispara nos casos certos.

Aceite: suíte verde; comportamento default de tipos documentado; nenhum reforço
infinito possível.

## Tarefa D (P1) — Enforce como "deny com pedagogia"

Decisão de design (dos críticos): o hook vê path, não conteúdo — `enforce:` não
valida a regra, só bloqueia path. O deny nativo (`permissions.deny`) já faz
isso; o valor agregado é (a) o corpo da regra como razão pedagógica do deny e
(b) ergonomia de autoria. Trust: escopo de projeto é untrusted ⇒ o hook só honra
`enforce:` do ESCOPO GLOBAL.

1. `frontmatter.py`: parse opcional de `enforce: deny` (qualquer outro valor ⇒
   ignorado com warn no CLI validate, nunca no hook).
2. Hook (`main.py`): quando uma regra do escopo GLOBAL com `enforce: deny` casa
   com o path e a tool é de escrita (`Write|Edit|MultiEdit|NotebookEdit`):
   responder com `hookSpecificOutput.permissionDecision: "deny"` e
   `permissionDecisionReason` = corpo da regra (defanged pelo `neutralize()`
   existente). Regras enforce de escopo de projeto: IGNORADAS pelo hook
   (sem warn no hot path). Injeção normal (additionalContext) das demais regras
   continua funcionando na mesma chamada quando não houver deny.
3. Admin CLI: subcomando `enforce` com `--list` (mostra regras enforce e as
   entradas de deny nativas equivalentes) e `--sync` (escreve entradas
   `Write(<glob>)`/`Edit(<glob>)`/etc. em `permissions.deny` do
   `.claude/settings.json` do projeto — reusar a máquina de hardening existente
   do setup; idempotente: não duplicar entradas; criar settings.json mínimo se
   não existir; escrita atômica via mkstemp+replace como o resto do CLI).
4. `validate.py`: NOTE para regra enforce em escopo de projeto ("hook ignora;
   rode enforce --sync para gerar deny nativo").
5. Docs: parágrafo curto no README (seção da Tarefa B se já existir) explicando
   o modelo: deny nativo = bloqueio; enforce global = bloqueio + explicação.

Testes (test_hook.py + test_admin.py + test_security.py): deny dispara para
regra global enforce em tool de escrita, com corpo como reason; NÃO dispara para
Read; NÃO dispara para regra enforce de projeto (segurança!); `--sync`
idempotente; frontmatter enforce inválido não quebra o hook (fail-open).

Aceite: suíte verde; nenhum caminho permite que escopo untrusted cause deny.

## Tarefa E (P1) — Marcador de supersede em regra editada + consolidação

Problema: a dedup key inclui sha256 do corpo, então editar a regra já re-injeta
o texto novo — mas as cópias antigas continuam no transcript como instruções
contraditórias (conflitos pairwise são driver de colapso, arXiv:2608.02639).

1. `constants.py`: `SUPERSEDE_NOTICE` (uma linha, inglês, ex.: "This version
   supersedes any earlier occurrence of this rule in the conversation.").
2. Detecção: ao injetar uma regra, procurar em `seen` chaves com o MESMO prefixo
   `realpath(scope_dir)::name::` e hash diferente ⇒ é edição: prefixar o corpo
   injetado com `SUPERSEDE_NOTICE` (dentro do bloco da regra, antes do corpo,
   após defang) e REMOVER os entries antigos dessa regra do `seen` (limpeza).
3. Cuidado com o formato do bloco: manter exatamente a moldura
   `<rules-by-path>`/`---`/`</rules-by-path>` existente.
4. `CHANGELOG.md`: consolidar a seção Unreleased com bullets das tarefas C, D e
   E (A e B já foram adicionadas pela Tarefa B) — uma linha por feature, estilo
   das entradas existentes.

Testes: editar corpo mid-session ⇒ próxima injeção tem o prefixo e seen não
acumula entries velhos; regra nova ⇒ sem prefixo; truncation + supersede
convivem (ordem dos avisos estável).

Aceite: suíte verde; sem crescimento não-limitado de `seen` por regra editada.
