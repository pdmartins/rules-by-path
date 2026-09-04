# v0.4.0 — arquivo de config (RULE_TYPES + defaults de reforço) e doutrina de split

## Context

Hoje duas informações que o usuário quer controlar estão presas no código:

- **Os tipos de regra** (`RULE_TYPES = ("Business", "Architecture", "Convention")`,
  `rules-by-path-admin.py:190`) — hardcoded no CLI, repetidos por extenso na
  tabela do `skills/manage/SKILL.md:130`. Trocar a taxonomia hoje exige editar
  dois arquivos e manter os dois em sincronia.
- **Os defaults de reforço** (`DEFAULT_REMEMBER_TOKENS`/`DEFAULT_REMEMBER_CALLS`,
  `constants.py:53-54`) — ajustáveis só por env var, e iguais para toda regra,
  independentemente do que custa violá-la.

O que muda: os dois passam para um **`config.json` em três camadas** (plugin →
global → projeto), a taxonomia vira `BUSN`/`ARCH`/`CONV`/`OTHR` com nome e
propósito por tipo, cada tipo ganha seu default de reforço, e `remember_after`
é renomeado para `remember_again_after` em todo o repositório. Em paralelo, a
skill `manage` ganha a doutrina que faltava: **memória grande ou com tipos
misturados é quebrada em regras menores, e o glob é o mais granular que ainda
pega a falha.**

Resultado pretendido: mudar a taxonomia ou a cadência de reforço passa a ser
editar um JSON; e uma memória despejada de uma vez vira N regras curtas, cada
uma com o glob certo, em vez de um bloco único reinjetado inteiro a cada
lembrete.

---

## Decisões já fechadas (perguntadas nesta sessão)

| Decisão | Escolha |
|---|---|
| Onde vive a config | plugin (default versionado) → global (`~/.claude/rules-by-path/config.json`) → projeto (`<root>/.claude/rules-by-path/config.json`), nessa precedência, com **clamp** nos valores vindos do repo |
| Defaults de reforço | **por tipo**, materializados pelo CLI no frontmatter da regra no `add`; o hook continua com um único par default e nunca precisa saber o tipo |
| Nome do campo | `remember_after` → **`remember_again_after`** em todos os lugares |
| Regras `Business_`/`Architecture_`/`Convention_` | renomeadas pelo `migrate` |

### Decisão que tomo aqui (e é fácil de reverter)

O `config.json` **publicado com o plugin fica em inglês** — o repo é público e
a regra de code-quality manda todo texto de código em inglês. Os textos em
pt-br que você escreveu (`Regras de Negócio`, `Pílulas de Memórias`, …) entram
**verbatim na sua config global**, `~/.claude/rules-by-path/config.json`, que é
exatamente a camada que existe para isso. Na sua máquina você vê o que
escreveu; quem instalar o plugin vê o inglês.

---

## O formato

`config.json` — mesmas chaves nas três camadas, nenhuma obrigatória:

```json
{
  "rule_types": [
    {"prefix": "BUSN", "name": "Business Rules",
     "purpose": "Domain invariants — violating one makes the software wrong",
     "remember_again_after": "20k"},
    {"prefix": "ARCH", "name": "Architecture Decisions",
     "purpose": "Where new code goes, what it inherits, what to reuse before creating",
     "remember_again_after": "30k"},
    {"prefix": "CONV", "name": "Conventions & Definitions",
     "purpose": "House style, naming, formatting — what keeps the codebase consistent",
     "remember_again_after": "50k"},
    {"prefix": "OTHR", "name": "Memory Pills",
     "purpose": "Anything else worth remembering about these files",
     "remember_again_after": "never"}
  ],
  "legacy_type_prefixes": {"Business": "BUSN", "Architecture": "ARCH",
                           "Convention": "CONV"},
  "remember_again_after": {"tokens": "30k", "calls": "25 calls"}
}
```

- `rule_types` — substituído **inteiro** pela camada mais próxima que o declara
  (mesclar por prefixo produziria taxonomias híbridas que ninguém escreveu).
- `legacy_type_prefixes` e `remember_again_after` — mesclados por chave.
- `RULES_BY_PATH_REMEMBER_AGAIN_AFTER` (env) vence todas as camadas; o nome
  antigo `RULES_BY_PATH_REMEMBER_AFTER` segue aceito como fallback.
- Valores por tipo usam o parser que já existe (`parse_remember_after`):
  tokens (`20k`), chamadas (`25 calls`) ou `never`.

**Clamp do que vem do repositório** (camada de projeto, conteúdo não confiável):
tokens com piso de `MIN_REMEMBER_AGAIN_TOKENS` (1.000) e chamadas com piso novo
de 5 — sem isso um repo clonado define `1k` e passa a reinjetar em quase toda
chamada; no máximo 16 tipos; prefixo casando `^[A-Z][A-Z0-9]{1,7}$`; `name` e
`purpose` de até 120 chars, uma linha, imprimíveis (eles são ecoados para o
terminal e para o modelo); chave desconhecida é ignorada. Leitura do arquivo:
`O_NOFOLLOW`, só arquivo regular, teto de 32 KB, JSON inválido → warn no stderr
e a camada é ignorada. Falha de config **nunca** derruba injeção.

---

## Mudanças, por arquivo

### Novo: `plugins/rules-by-path/config.json`
O default publicado, com o conteúdo acima.

### Novo: `plugins/rules-by-path/hooks/rules_by_path/config.py` (~200 linhas)
- `read_config_file(path)` — leitura segura e limitada (espelha
  `rules.read_rule_file`, `rules.py:66`).
- `sanitize_config(raw, trusted)` — validação + clamp; `trusted=False` para
  camada de projeto.
- `load_config(scope_dirs)` — funde plugin + as camadas dadas, na ordem.
- `rule_types(config)`, `type_by_prefix(config, prefix)`,
  `remember_again_after_for_type(config, prefix)`.
- `remember_again_after_default(config, measured_in_tokens)` — substitui
  `frontmatter.remember_after_default` (`frontmatter.py:186`); env var primeiro,
  depois config, depois os fallbacks de `constants.py`.

### `constants.py`
Renomes: `DEFAULT_REMEMBER_TOKENS`→`DEFAULT_REMEMBER_AGAIN_TOKENS`,
`DEFAULT_REMEMBER_CALLS`→`DEFAULT_REMEMBER_AGAIN_CALLS`,
`MIN_REMEMBER_TOKENS`→`MIN_REMEMBER_AGAIN_TOKENS`,
`REMEMBER_ENV_VAR`→`REMEMBER_AGAIN_ENV_VAR` (+ `LEGACY_REMEMBER_ENV_VAR`).
Novos: `MIN_REMEMBER_AGAIN_CALLS`, `CONFIG_FILE_NAME`, `PLUGIN_CONFIG_PATH`,
`MAX_CONFIG_BYTES`, `MAX_RULE_TYPES`, `MAX_TYPE_TEXT_CHARS`, `TYPE_PREFIX_RE`.
Os `DEFAULT_*` passam a ser documentados como **fallback de última instância**
(usados só se o `config.json` do plugin sumir ou não parsear).

### `frontmatter.py`
`parse_remember_after`→`parse_remember_again_after`,
`remember_after_of`→`remember_again_after_of`, lendo a chave
`remember_again_after` e aceitando `remember_after` como **alias legado**
(silencioso no hook — princípio de nunca derrubar regra de terceiro; o
`validate` é quem emite a nota). `remember_after_default` sai daqui e vira
`config.remember_again_after_default`.

### `main.py`
`main()` já tem `scopes`; passa a chamar `load_config([scope_dir for …])` uma
vez por chamada e a usar o default vindo dali (`main.py:123`). Custo: até 8
`os.open` que falham em cenário típico — irrelevante frente aos ~100ms do hook.

### `scripts/rules-by-path-admin.py`
- `RULE_TYPES`/`RULE_NAME_CONVENTION` (linhas 190-192) deixam de ser literais:
  passam a ser derivados da config carregada para o escopo alvo.
- **`--type`** em `add`: normaliza (case-insensitive) contra os prefixos
  configurados; prefixa o nome (`--rule handlers-inherit-base.md` +
  `--type ARCH` → `ARCH_handlers-inherit-base.md`); conflito entre `--type` e um
  `--rule` já prefixado → erro. **`add` passa a exigir o tipo** (via `--type` ou
  nome prefixado), com mensagem listando os tipos configurados e seus propósitos
  — é o único ponto do sistema em que existe um humano para perguntar, e é o que
  faz a skill parar de chutar. `update` não mexe no nome (trocar de tipo =
  `remove` + `add`).
- Materialização: `remember_again_after` = flag `--remember-again-after` >
  frontmatter vindo do stdin > default do tipo na config; quando vem do tipo,
  é escrito explícito no arquivo e o `ok:` diz de onde veio.
- Flag `--remember-after` **sai** (renome pedido; `migrate` cobre o legado).
  `OWN_KEYS` ganha `remember_again_after`.
- **Novo subcomando `config`** — imprime a config efetiva do escopo: tipos
  (prefixo, nome, propósito, reforço) e os defaults, dizendo de qual camada cada
  bloco veio. É por aqui que a skill descobre os tipos, em vez de carregá-los
  hardcoded no SKILL.md.
- **`migrate` deixa de ser só "converter rules-map.yml"** e passa a ser "trazer
  este escopo para o formato atual", em três passos idempotentes: (1) o
  `rules-map.yml` de hoje; (2) renomear prefixos legados via
  `legacy_type_prefixes` (`Business_x.md` → `BUSN_x.md`, com as mesmas guardas de
  `existing_is_not_a_rule`/symlink/`--force` já usadas); (3) reescrever
  `remember_after:` → `remember_again_after:` no frontmatter. Regra **sem**
  prefixo (como a sua `hv-dotnet-stack.md`) não é adivinhada: é reportada como
  "precisa de tipo", porque o tipo é juízo de quem é dono do código.
- `validate`: nota de nome fora da convenção passa a listar os tipos
  configurados; nota nova para `remember_after:` legado e para chave
  desconhecida na config.

### `skills/manage/SKILL.md` — a parte 2 do pedido
1. A tabela hardcoded de tipos (linha 130) vira: rode
   `rules-by-path config --root <root>`, que imprime os tipos configurados —
   uma fonte da verdade só.
2. Seção nova **"Uma memória chega grande"**: antes de escrever, decompor.
   Uma regra por **tipo** e por **conjunto de caminhos**; cada fragmento tem de
   se sustentar sozinho (regra não referencia regra); acima de ~1.200 chars,
   procurar a divisão antes de gravar; ao final, `validate`. Encaixa na seção
   "One rule, one scope" que já existe (linha ~200) — a doutrina de path set
   fica, ganha o eixo de tipo e o gatilho de tamanho.
3. Seção nova **"O glob mais granular que ainda pega a falha"**: o default é o
   glob mais estreito que cobre os arquivos governados; ampliar só com motivo
   declarado. O contra-exemplo que já está na spec §4 fica explícito: regra
   anti-duplicação precisa do glob na área *errada* (`src/Application/**`, não
   `src/Application/Enums/**`), porque o caminho canônico é justamente o que o
   agente não toca. Procedimento: identificar qual arquivo o agente estará
   tocando **no momento em que violaria a regra**, e globar aquilo.
4. Exemplos de `add` com `--type` e `--remember-again-after`.

### Docs e restos
`README.md` (seção "Repeating a rule" + seção nova de config),
`skills/setup/SKILL.md:77` (env var), `commands/status.md:61,86` (env var +
imprimir a config efetiva), `hooks/rules_by_path/__init__.py` (docstring e
re-exports do módulo novo), `CHANGELOG.md` (entrada `0.4.0`; a pendência da
`0.2.0` continua aberta e fora deste escopo).

### Sua config global
Escrever `~/.claude/rules-by-path/config.json` com os quatro tipos e os textos
em pt-br exatamente como você os passou.

---

## Testes

- **`tests/test_config.py` (novo)** — precedência das três camadas; clamp de
  valores de projeto (tokens < 1.000 e chamadas < 5 sobem para o piso); JSON
  inválido/arquivo gigante/symlink → camada ignorada, injeção segue; tipos
  demais e prefixo inválido recusados; chave desconhecida ignorada.
- **`tests/test_admin.py`** — `add` sem tipo falha listando os tipos; `--type`
  prefixa o nome; conflito `--type` vs nome prefixado; default do tipo escrito no
  frontmatter; `config` imprime a camada certa; `migrate` renomeia
  `Business_*`→`BUSN_*`, reescreve a chave e reporta a regra sem prefixo.
- **`tests/test_hook.py`** — default vindo da config global e sobreposto pela de
  projeto; env var vence tudo; `remember_after:` legado ainda honrado;
  fallback em chamadas quando o transcript não é legível.
- **`tests/test_security.py`** — config de projeto hostil não consegue reduzir o
  intervalo abaixo do piso nem injetar texto pelos campos `name`/`purpose`.

## Verificação

```bash
python3 -m pytest tests/ -q                     # suíte inteira verde
python3 plugins/rules-by-path/scripts/rules-by-path-admin.py config --global
claude plugin validate plugins/rules-by-path    # empacotamento
```

Smoke manual, em escopo temporário: `add --type BUSN` → conferir que o arquivo
saiu `BUSN_*.md` com `remember_again_after: 20k`; rodar o hook com um payload
tocando o arquivo coberto e ver a regra injetada; gravar um `config.json` de
projeto com `{"remember_again_after": {"tokens": "5k"}}` e conferir pelo estado
da sessão que o intervalo efetivo é 5k; repetir com `500` e conferir o clamp.
Por fim, `migrate` num escopo com `Business_x.md` + `remember_after:` e conferir
nome e chave reescritos.

## Fora de escopo (registrado)

`rules-by-path-admin.py` tem 900 linhas e vai para ~1.030 — já viola a regra de
< 400 linhas por arquivo. Quebrá-lo em módulos (`config`, `crud`, `validate`,
`migrate`) é um commit próprio, com movimentação verbatim e a suíte como prova.
Digo quando terminar, para você decidir se quer na sequência.
