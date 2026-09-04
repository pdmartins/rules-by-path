# rules-by-path — design das correções de segurança e robustez

Contexto: auditoria multi-agente (5 finders + verificação adversarial) sobre o
plugin recém-portado. Este documento fecha as decisões de design das correções
não triviais. Achados mecânicos (case-sensitivity, ordem de cleanup, `python3`)
não precisam de design, só de correção.

## 1. Contenção de arquivos de regra (A1, A2)

**Problema.** `read_rule_content` valida `dirname(realpath(rule_file)) ==
realpath(rules_dir)`. Se `rules/` for ele próprio um symlink para `/etc`, os
dois lados resolvem para `/etc` e a checagem passa — qualquer arquivo legível
vira contexto. O admin tem o espelho do problema: escreve e apaga através do
symlink.

**Decisão.** A contenção passa a ser ancorada no diretório do **mapa**, não no
diretório de regras:

```
map_dir   = realpath(dirname(map_path))
rules_dir = realpath(map_dir/rules)
exigir: rules_dir == map_dir + "/rules"     (rules/ não pode ser symlink)
exigir: realpath(rule_file) == rules_dir + "/" + name   (arquivo não escapa)
exigir: not islink(rule_file)                (nem o arquivo em si)
```

Isto é uma única função `resolve_rule_path(map_path, rule_name)` usada pelo hook
**e** pelo admin — a divergência entre os dois é que criou A2. Falha → warn +
skip (hook) / fail (admin).

Custo: symlinks legítimos para `rules/` deixam de funcionar. Aceitável: não é um
caso de uso real, e a alternativa é leitura arbitrária de arquivos.

## 2. Provenance não-forjável (A3, A4, D5)

**Problema.** O bloco injetado usa delimitadores em texto puro
(`--- Rule 'x' (scope: global, ...) ---`) e concatena o conteúdo verbatim. Uma
regra de um repo clonado emite seu próprio delimitador e se declara `global`.
O nome da regra, vindo do mapa, é interpolado sem validação (segundo vetor).

**Decisão.** Três camadas, todas necessárias:

1. **Nonce por invocação.** O header abre com um token aleatório
   (`secrets.token_hex(8)`) e cada delimitador o carrega:
   `--- rule <nonce> | scope: project /repos/x | glob: 'src/**' ---`.
   O conteúdo não pode adivinhar o nonce, então não pode forjar um bloco. O
   header declara explicitamente: só blocos com este token são autênticos.
2. **Sanitização do conteúdo.** Qualquer linha do conteúdo que comece com
   `--- rule ` é prefixada com um caractere de escape, e o nonce, se por
   acaso aparecer, é removido. Defesa em profundidade sobre (1).
3. **Validação do nome da regra.** Nomes de regra passam a exigir
   `^[A-Za-z0-9._+-]+\.md$` — sem aspas, sem espaços, sem newline. Isso também
   fecha A4 e alinha com o que o admin já deriva.

O README passa a descrever a garantia real, não uma mais forte (D5).

## 3. Matching de glob sem backtracking exponencial (C1)

**Problema.** `**/` vira `(?:[^/]+/)*` — quantificador aninhado. Um glob de 43
caracteres com poucos `**/` produz backtracking exponencial; o hook queima uma
CPU e estoura o timeout de 10s **a cada tool call**. O cap de 256 chars não
protege.

**Decisão.** Abandonar regex. Matcher próprio, em dois níveis, ambos com
complexidade polinomial garantida:

- **Nível path**: segmentos separados por `/`. `**` casa zero ou mais
  segmentos. Algoritmo de dois ponteiros com backtrack em `**` (o clássico de
  wildcard matching): O(n·m) pior caso, linear no caso comum.
- **Nível segmento**: `*` (não cruza `/`) e `?`. Mesmo algoritmo de dois
  ponteiros: O(n·m).

Sem regex, sem `re.error`, sem ReDoS por construção. Os testes de semântica de
glob existentes viram a especificação executável desta reescrita — todos devem
continuar passando sem alteração.

## 4. Fronteira de confiança do walk-up (A6, C2)

**Problema.** `find_rule_sources` sobe até `/` e confia em qualquer
`.claude/rules-by-path/rules-map.yml` que encontrar, rotulando como escopo
"project". Um mapa plantado em um diretório pai compartilhado (world-writable)
injeta instruções para tudo abaixo. Sem cap, também é o vetor de C2.

**Decisão.** Três limites:

1. **Parar na raiz do repositório.** O walk-up termina no primeiro diretório
   que contém `.git` (inclusive). Fora de repositório, para no `cwd` da sessão.
   Isso alinha o escopo "project" com o que o usuário entende por projeto.
2. **Cap de mapas.** No máximo 8 mapas por tool call (raiz→arquivo + global).
3. **Recusar diretórios inseguros.** Um mapa cujo diretório seja
   world-writable (`stat.S_IWOTH`) e não pertença ao usuário é ignorado com
   warning. Fecha o cenário `/tmp` compartilhado.

## 5. Dedup por conteúdo (B2)

**Problema.** Chave = `realpath(mapa)::nome`. Após `add --force`, o hook
considera a regra já injetada e o modelo segue a versão antiga pelo resto da
sessão — falha silenciosa, exatamente o cenário de uso mais comum (iterar
sobre uma regra).

**Decisão.** Chave passa a incluir um hash curto do conteúdo:
`realpath(mapa)::nome::sha256(conteúdo)[:16]`. Editar a regra muda a chave e
força a re-injeção. Custo: um sha256 por regra casada (desprezível).

## 6. Escrita atômica e não destrutiva do mapa (B1, B3)

**Problema.** `load_raw_entries` devolve `[]` tanto para "mapa vazio" quanto
para "não consegui parsear / grande demais". `cmd_add` faz read-modify-write →
um mapa ilegível é **apagado inteiro** com relatório de sucesso. O parser
fallback ainda corta globs no `#` dentro de aspas, corrompendo entradas.

**Decisão.**

- `load_raw_entries` passa a devolver `(entries, ok)`. O hook trata `ok=False`
  como "sem regras" (fail-open, como hoje); o admin **aborta** qualquer escrita
  quando `ok=False`, com mensagem dizendo qual mapa está ilegível.
- O parser fallback passa a respeitar aspas ao remover comentários.
- Escrita do mapa vira atômica: grava em `.tmp` no mesmo diretório e
  `os.replace` — sem janela em que o mapa fica truncado.
- O admin escreve globs com `json.dumps` (aspas/backslash corretamente
  escapados) em vez de f-string ingênua.

## 7. Glob nunca volta para uma linha de comando (A5)

**Problema.** `which` imprime o glob controlado pelo repo, e tanto a skill
quanto a mensagem de deny do hook mandam o modelo colar aquele glob em
`add --glob '<glob>'`. Um glob com aspas simples escapa do quoting.

**Decisão.** Duas medidas:

1. `which` ganha `--json` e a skill passa a instruir o uso dele; a mensagem de
   deny deixa de mandar colar o glob literal.
2. O admin ganha `update --rule <nome>` — atualizar uma regra passa a
   referenciar o **nome do arquivo** (validado por `^[A-Za-z0-9._+-]+\.md$`),
   nunca o glob. O glob só aparece em linha de comando quando o **usuário** o
   está criando, não quando vem de dados do repo.

## 8. Invocação e estado (D1, D2, D6)

- **Interpretador (D1).** `hooks.json` passa a chamar um launcher `bin/` em vez
  de `python3` literal. `bin/rules-by-path-hook` (sh) resolve o primeiro de
  `python3`/`python`/`py -3` que exista; `bin/rules-by-path-hook.cmd` faz o
  mesmo no Windows. Confirmado empiricamente que `bin/` entra no PATH.
- **Estado (D2).** `CLAUDE_PLUGIN_DATA` quando definido, senão
  `~/.claude/cache/rules-by-path`, senão `tempfile.gettempdir()`. Se nenhum for
  gravável, o hook **avisa uma vez** em vez de re-injetar silenciosamente a
  cada chamada.
- **Ergonomia (D6).** `bin/rules-by-path` expõe o admin como comando curto; as
  skills passam a usar `rules-by-path add --root ...` em vez do caminho longo
  com `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/..."`. Menos tokens por chamada e
  menos superfície de erro.

## 9. Documentação (D3, D4)

- `admin show --rule <nome>` — canal sancionado para ler o conteúdo de uma
  regra, já que o hardening bloqueia `cat`/`Read`. Sem isso, atualizar uma
  regra existente é impossível sob o hardening que o próprio plugin recomenda.
- A skill `setup` ganha uma seção de **desinstalação** que remove as deny rules,
  e o README documenta o procedimento.

## Ordem de implementação

1. Matcher de glob (3) — é a base, e os testes existentes validam a reescrita.
2. Contenção compartilhada (1) + validação de nome (2.3).
3. Parser/escrita do mapa (6).
4. Provenance (2.1, 2.2) + dedup por conteúdo (5).
5. Walk-up (4).
6. Launcher/estado/bin (8) + admin `show`/`update` (7, 9).
7. Correções mecânicas: B4, C3, D3/D4 docs, README.
8. Testes para cada achado (regressão) + suíte completa verde.
