# TODO — validação arquivo por arquivo do plugin (2026-08-21)

Protocolo: um item por vez, do mais crítico ao menos; só avança após o Pedro validar
o anterior. Achados viram correção + teste que falha sem ela. Baseline: `pytest tests -q`
→ 321 passed (era 317 antes das correções do item 1). Teto de 400 linhas por arquivo.

Espelhado na task list nativa da sessão (TaskList).

## Hook — o motor
- [x] 1. `main.py` (338) — orquestração, enforce/deny, montagem dos blocos
      Extraído `matching.py` (102): caminho tocado e regras que ele casa, movido
      verbatim para caber no teto. Os 4 achados abaixo estão corrigidos e cobertos
      por `tests/test_main_validation.py`.  ← AGUARDANDO VALIDAÇÃO DO PEDRO
- [ ] 2. `state.py` (380) — dedup, tokens, supersede, regressão de contexto
- [ ] 3. `config.py` + `configfile.py` + `reinject.py` (470) — 3 camadas, projeto é untrusted
- [ ] 4. `context.py` + `messages.py` (258) — texto injetado, defang, i18n
- [ ] 5. `rules.py` + `discovery.py` (273) — leitura segura, escopos, ownership
- [ ] 6. `globbing.py` (93) — matcher
- [ ] 7. `constants.py` + `frontmatter.py` (459) — REVALIDAR (mudaram após a 1ª validação)
- [ ] 8. fiação: `__init__.py`, facade, `hooks.json`, 6 launchers `bin/`

## Admin — a CLI que escreve as regras
- [ ] 9. `common.py` (264) — containment, atomic write
- [ ] 10. `rules.py` (259) — add/update/remove/show/list/which
- [ ] 11. `validate.py` + `config.py` (468)
- [ ] 12. `enforce.py` (161) — escreve em `permissions.deny` nativo
- [ ] 13. `migrate.py` + `cli.py` + facade + `__init__.py` (473)

## Superfície de instrução
- [ ] 14. `skills/manage/SKILL.md` (441 — acima do teto, quebrar), `skills/setup`,
      `commands/status.md`, `config.json`

## Achados do item 1 — corrigidos
1. [x] `path_targets`: `os.path.relpath` levanta `ValueError` no Windows quando o
   alvo resolvido está em outro volume (junction) — derrubava a injeção inteira da
   call. Agora tratado como "saiu do projeto"; provado com `ntpath`.
2. [x] Aviso de formato legado gravava entrada `seen` com 2 elementos; agora 3,
   como todo o resto.
3. [x] Esse mesmo aviso era anexado sem contar contra `MAX_TOTAL_CHARS`; agora é
   orçado como um bloco qualquer e, se não couber, fica para a próxima call em vez
   de ser marcado como dito.
4. [x] `reset_session` agora tem o `try` no `json.load` que `session_notice` tem —
   um payload ilegível não vira mais `unexpected error` no stderr.

## Fora desta validação (herdado da auditoria de 19/08, P2)
- lint de co-injeção de globs sobrepostos
- telemetria de injeção com retenção própria + comando `audit`
- `.pytest_cache/` criado dentro de `scripts/rules_by_path_admin/` (higiene)
