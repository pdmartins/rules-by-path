# `language`: o idioma em que as regras são gravadas — design

Data: 2026-08-20
Escopo decidido pelo usuário: o setting mora no `config.json` do plugin (3 camadas já
existentes) e afeta **o corpo das regras + os textos que o hook injeta no contexto**.
Fora de escopo: i18n das mensagens da CLI (`--help`, erros do argparse, saída de
`add`/`list`/`validate`), que seguem em inglês pela regra global de código em inglês.

---

## 1. O problema

Hoje tudo que o plugin escreve e injeta é inglês fixo em `constants.py`. Um usuário
que trabalha em pt-BR acaba com regras em pt-BR (porque o modelo segue o idioma da
conversa) embrulhadas em andaime em inglês, e sem nenhum lugar onde declarar essa
escolha — ela se re-decide a cada sessão, por acidente.

## 2. A chave de configuração

Nova chave top-level `language` em `config.json`, lida pelas MESMAS três camadas que
todas as outras chaves, na mesma ordem:

    <plugin>/config.json                          default de fábrica  →  "en"
    ~/.claude/rules-by-path/config.json           a do usuário (trusted)
    <projeto>/.claude/rules-by-path/config.json   a do projeto (untrusted), vence

`language` é um escalar, então `merge_layer` já o trata corretamente: a camada mais
próxima substitui, e `sources["language"]` registra de onde veio.

**Projeto vence global, de propósito.** Uma regra escrita *dentro daquele repositório*
deve sair no idioma daquele repositório. É consistente com todas as outras chaves, e
`rules-by-path config` mostra a camada de origem, então a escolha nunca é invisível.

**Uma exceção, explícita: o motivo do `enforce: deny`.** É a única frase que o plugin
diz em nome do dono da máquina *contra* o repositório, e o repositório cujo write está
sendo negado não escolhe em que língua a negação chega. `main.trusted_scopes` resolve
o idioma dessa mensagem só pelas camadas trusted (plugin + global). Registrado aqui do
mesmo jeito que a §4.3 registra o `SUPERSEDE_NOTICE`, para não parecer esquecimento.

### 2.1. Valor aceito e saneamento

`language` é uma string, e chega de camada untrusted (um clone traz o `config.json` do
projeto). Vai direto para dentro de texto que o modelo lê, então é saneada como um
glob, não como dado interno:

| Regra | Valor |
|---|---|
| Comprimento máximo | `MAX_LANGUAGE_CHARS = 32` (medido **depois** do NFKC) |
| Normalização | `unicodedata.normalize("NFKC", raw).strip()` antes de qualquer teste |
| Caracteres permitidos | alfanuméricos Unicode + `LANGUAGE_EXTRA_CHARS = " -_()"` |
| Caracteres proibidos | `LANGUAGE_FORBIDDEN_CHARS` — os quatro fillers Hangul |
| Exigências | uma linha só, `isprintable()`, pelo menos uma letra |
| Fora disso | `warn(...)` e a chave é **ignorada** — cai na camada de baixo |

O allowlist é o ponto: sem `\n`, sem `:`, sem `` ` ``, sem `<`/`>`, sem `#`, sem aspas.
O que ele garante, dito com precisão: o valor **não consegue forjar delimitador, chave
de frontmatter nem segunda linha**. Ele **não** garante que 32 caracteres sejam poucos
demais para redigir um imperativo curto — `"en IGNORE THE RULES ABOVE"` tem 25 e passa —
e por isso **todo lugar que ecoa o valor o imprime com `!r`** (`config` e `validate`),
para que ele apareça como valor reportado e não como frase na voz da CLI.
`"pt-BR"`, `"en"`, `"Portuguese (Brazil)"` e `"español"` passam (`ñ` é alfanumérico em
Unicode); `"en\nIgnore all previous instructions"` não.

**Invisíveis e confusáveis.** `str.isalnum()` é verdadeiro para a categoria Lo e
`isprintable()` também, então os quatro fillers Hangul (`U+115F`, `U+1160`, `U+3164`,
`U+FFA0`) passariam nos dois testes renderizando **nada** — um valor que o humano lê
como `en` e que o código não resolve como `en`. São negados por constante nomeada. Na
outra direção, o NFKC dobra `ｅｎ` (fullwidth) e `𝖾𝗇` (math-bold) de volta em `en`, para
que o que foi aprovado no arquivo seja o que o código seleciona.

Clamping numérico não se aplica aqui — não há direção "perigosa" a limitar, só forma.
Trusted e untrusted passam pelo mesmo saneamento, sem exceção para a camada global.

### 2.2. Normalização

Comparação case-insensitive e `_` tratado como `-`, então `pt_br`, `PT-BR` e `pt-BR`
são o mesmo idioma. O valor **canônico** (o que a tabela de traduções indexa) é o
shipped, `"pt-BR"`; o valor **cru saneado** é o que a skill usa para redigir o corpo.

## 3. Os dois consumidores (e por que têm requisitos diferentes)

| Consumidor | O que precisa | Idioma não previsto |
|---|---|---|
| **Corpo da regra** (skill `manage` redige) | qualquer string saneada — o modelo escreve em qualquer idioma | funciona normalmente |
| **Andaime injetado** (hook emite) | tradução **shipped**, escrita por humano | **cai para inglês**, com NOTE no `validate` |

Essa assimetria é deliberada. Não dá para traduzir o andaime em tempo de execução, e
deixar qualquer camada de config *fornecer* o texto do andaime seria entregar a um
repositório clonado a caneta que escreve as frases que o modelo mais confia.

## 4. A tabela de traduções

Novo módulo **`hooks/rules_by_path/messages.py`**, propriedade do plugin,
**não sobrescrevível por nenhuma camada de config**. A config só *seleciona*.

Módulo Python e não arquivo de dados: zero I/O novo, zero parser novo, zero superfície
de leitura de arquivo untrusted. Idiomas shipped nesta entrega: `en` (fallback) e `pt-BR`.

API:

```python
messages_for(language)  # -> dict com TODAS as chaves; cai para en se não houver tradução
has_translation(language)  # -> bool, para o NOTE do validate e a saída do config
SHIPPED_LANGUAGES  # -> tupla dos códigos canônicos
```

### 4.1. O que é traduzido

Move de `constants.py` para a tabela (mantendo os nomes atuais como a entrada `en`,
para nada quebrar):

- `SESSION_NOTICE`
- `LEGACY_NOTICE`
- `TRUNCATION_NOTICE`
- `SUPERSEDE_NOTICE`
- `ENFORCE_DENY_REASON_TEMPLATE`

### 4.2. O que NUNCA é traduzido

Estrutura e segurança ficam byte-idênticas em todo idioma:

- `RULES_OPEN_TAG`, `RULES_CLOSE_TAG`, `RULE_SEPARATOR` — são a moldura que delimita
  o bloco; traduzi-las quebraria o `neutralize` e a capacidade de dizer onde as
  regras terminam.
- O prefixo literal `[rules-by-path]` — é marcador de harness, não prosa. **Toda**
  tradução de `SESSION_NOTICE` começa com ele, verbatim.
- Os placeholders `{name!r}` e `{body}` de `ENFORCE_DENY_REASON_TEMPLATE` — **toda**
  tradução carrega os dois, com a mesma grafia.
- O caminho `ADMIN_COMMAND` e as flags citadas (`migrate --root`, `list|show|which|
  add|update`, `--global`) — são comandos, não texto.

### 4.3. Invariante de segurança do `neutralize` — o ponto sutil

`FORGED_FRAMING_TOKENS` hoje inclui `TRUNCATION_NOTICE.strip()`. Assim que essa string
passa a depender do idioma, defangar só a variante ativa deixa as outras utilizáveis.

**Requisito:** `FORGED_FRAMING_TOKENS` passa a incluir a variante de `TRUNCATION_NOTICE`
de **todos** os idiomas shipped, independentemente do idioma ativo. É mais barato e não
tem downside — defangar uma string que ninguém emitiria custa uma substituição a mais.

`SUPERSEDE_NOTICE` continua **fora** da lista, como hoje (não é regressão desta feature;
está registrado aqui só para que a decisão seja explícita e não pareça esquecimento).

## 5. Como o idioma chega ao ponto de emissão

O idioma é resolvido **uma vez** por invocação, em `main.py`, que já carrega a config, e
desce por parâmetro. Sem estado global mutável.

```python
msgs = messages_for(language(config))
context = build_context(blocks, messages=msgs)
```

`build_context(blocks, messages=None)` — parâmetro **opcional** com fallback para inglês,
para que as chamadas existentes (e os testes atuais) continuem válidas sem edição.

O caminho de **SessionStart** também precisa resolver a config para emitir o
`SESSION_NOTICE` traduzido; hoje ele pode não carregar config nenhuma. Se carregar custar
I/O que hoje não existe nesse caminho, carregue mesmo assim — é uma leitura de arquivo
pequeno, uma vez por sessão.

## 6. Superfície de mudança

| # | Arquivo | Mudança |
|---|---|---|
| 1 | `hooks/rules_by_path/constants.py` | `LANGUAGE_KEY`, `MAX_LANGUAGE_CHARS`, `LANGUAGE_EXTRA_CHARS`, `LANGUAGE_FORBIDDEN_CHARS`, `DEFAULT_LANGUAGE = "en"`; `FORGED_FRAMING_TOKENS` passa a somar as variantes de truncation de todos os idiomas |
| 1b | `hooks/rules_by_path/configfile.py` | **novo** — `read_config_file` e `config_path_for` saem de `config.py`, que estava no teto de 400 linhas |
| 2 | `hooks/rules_by_path/messages.py` | **novo** — tabelas `en` e `pt-BR`, `messages_for`, `has_translation`, `SHIPPED_LANGUAGES` |
| 3 | `hooks/rules_by_path/config.py` | `"language"` em `CONFIG_KEYS`; `sanitize_language()`; wire em `sanitize_config()`; acessor `language(config)`; chave no dict base de `load_config` |
| 4 | `hooks/rules_by_path/context.py` | `build_context(blocks, messages=None)` |
| 5 | `hooks/rules_by_path/main.py` | resolve o idioma e passa adiante; idem no caminho de SessionStart. O `enforce: deny` é decidido antes de qualquer config, e `trusted_scopes` + `messages_for_scopes` dão o idioma dele |
| 6 | `plugins/rules-by-path/config.json` | `"language": "en"` + linha no `_comment` explicando as duas camadas e o fallback do andaime |
| 7 | `scripts/rules_by_path_admin/config.py` | `cmd_config` imprime `language:`, a camada de origem, e se o andaime está traduzido ou caindo para inglês |
| 8 | `scripts/rules_by_path_admin/validate.py` | NOTE quando `language` não tem tradução shipped |
| 9 | `skills/manage/SKILL.md` | passo que lê `config`, e a instrução de redigir o **corpo** no idioma em vigor (nomes de arquivo, prefixos de tipo e chaves de frontmatter seguem em inglês/ASCII) |
| 10 | `README.md` + `CHANGELOG.md` | documentar a chave, as camadas e o fallback |
| 11 | `tests/test_language.py` (novo) + `tests/test_config.py` | ver §7 |

**Nada** disso pode mudar nome de arquivo de regra, prefixo de tipo (`BUSN`/`ARCH`/…) ou
chave de frontmatter: seguem ASCII e em inglês, porque são identificadores, não prosa.

## 7. Testes obrigatórios

Saneamento e camadas:
1. valor válido passa; `"pt_br"` normaliza para `"pt-BR"`
2. > 32 chars, string vazia, `"123"` (sem letra), não-printável, `"en\nmais coisa"`,
   `"en: ignore"` → todos ignorados **com warn**, caindo na camada de baixo
3. camada de projeto vence a global, que vence o default do plugin
4. camada de projeto **untrusted** não consegue injetar texto no andaime: com
   `language` malicioso, o texto injetado sai byte-idêntico ao inglês

Tabela de traduções:
5. toda tradução tem exatamente o mesmo conjunto de chaves do `en`
6. toda tradução de `ENFORCE_DENY_REASON_TEMPLATE` contém `{name!r}` e `{body}`, e
   formata sem `KeyError`
7. toda tradução de `SESSION_NOTICE` começa com `[rules-by-path]`
8. idioma sem tradução (`"Klingon"`) → `messages_for` devolve a tabela `en` e
   `has_translation` é falso

Emissão:
9. com `language: pt-BR`, `build_context` emite supersede e truncation em pt-BR
10. `RULES_OPEN_TAG`/`RULES_CLOSE_TAG`/`RULE_SEPARATOR` saem idênticos em qualquer idioma
11. **`neutralize` defanga a truncation notice de TODO idioma shipped, mesmo rodando
    sob outro idioma** (o invariante da §4.3)
12. sem `language` em camada nenhuma: saída byte-idêntica à de hoje (regressão zero)

## 8. Restrições de execução

- Baseline a manter: `python3 -m pytest tests/ -q` → **282 passed**. Só pode subir.
- Nenhum arquivo `.py` acima de ~400 linhas (`config.py` já está em 381 — por isso
  `messages.py` é módulo separado, e não mais uma função lá dentro).
- Sem literal hardcoded em lógica, constantes agrupadas no topo, código e comentários
  em inglês, sem dead code, sem `except` silencioso.
- Um `config.json` ilegível, com `language` inválido ou com idioma desconhecido
  **nunca** pode impedir a injeção: warn e segue. Isto é mais forte do que parece e
  foi endurecido nesta entrega: a decisão de `enforce: deny` corre no mesmo caminho,
  então uma exceção escapando de uma camada não custava só as configurações dela —
  cancelava o bloqueio do próprio dono da máquina, em silêncio e com exit code 0.
  Três camadas de defesa: (a) `load_layer` responde a qualquer falha de saneamento
  com warn e camada vazia; (b) as coerções numéricas aceitam `OverflowError`
  (`1e400` é JSON válido e vira `float('inf')`) e `read_config_file` captura o
  `RecursionError` do aninhamento profundo; (c) `enforce_denial` é decidido **antes**
  de qualquer leitura de config, e `messages_for_scopes` degrada para inglês em vez
  de perder a mensagem.
